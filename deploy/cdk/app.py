"""Thought Coach on AWS (CDK). Scale to zero: only the upload API, storage and the website are
always there (pennies); the review worker starts when a game arrives and switches itself off.

    extension -> https://<domain>/api/recordings  (CloudFront -> HTTP API -> Lambda)
              -> S3 (recording) + SQS (job) + wake the worker (Auto Scaling group 0 -> 1)
    worker    -> reviews, publishes pages to the site bucket, strips the audio, switches off
    you       -> https://<domain>/games/<id>/story   (CloudFront -> site bucket)

Deploy (from deploy/cdk):  see deploy/README.md
"""

from __future__ import annotations

import os
import zipfile

import aws_cdk as cdk
from aws_cdk import (
    Duration, RemovalPolicy, Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_autoscaling as autoscaling,
    aws_certificatemanager as acm,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_ec2 as ec2,
    aws_codebuild as codebuild,
    aws_ecr as ecr,
    aws_s3_assets as s3_assets,
    aws_s3_deployment as s3_deploy,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_s3 as s3,
    aws_sqs as sqs,
)
from constructs import Construct

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKER_TYPE = "c7i.xlarge"  # 4 vCPU, 8 GB; only runs while reviewing

# /games/<id> and /games/<id>/story are folders in the site bucket: serve their index.html.
REWRITE = """function handler(event) {
  var r = event.request;
  if (r.uri.endsWith('/')) { r.uri += 'index.html'; }
  else if (r.uri.indexOf('.') === -1) { r.uri += '/index.html'; }
  return r;
}"""


SITE_DIR = os.path.join(ROOT, "deploy", "site")


def build_extension_zip() -> None:
    """The extension as a download for the setup page (until it is in the Chrome Web Store)."""
    src = os.path.join(ROOT, "extension")
    with zipfile.ZipFile(os.path.join(SITE_DIR, "thoughtcoach-extension.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(src)):
            if name.endswith((".js", ".json", ".html", ".png")):
                z.write(os.path.join(src, name), f"thoughtcoach-extension/{name}")


class ThoughtCoach(Stack):
    def __init__(self, scope: Construct, cid: str, *, domain: str | None, upload_key: str, owner: str | None,
                 **kw) -> None:
        super().__init__(scope, cid, **kw)

        # ---- storage and queue
        data = s3.Bucket(self, "Data", encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True,
                         block_public_access=s3.BlockPublicAccess.BLOCK_ALL, removal_policy=RemovalPolicy.RETAIN,
                         # The extension uploads recordings straight here with a one-time signed link.
                         cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.PUT], allowed_origins=["*"],
                                           allowed_headers=["*"], max_age=3000)],
                         lifecycle_rules=[s3.LifecycleRule(prefix="incoming/", expiration=Duration.days(1))])
        site = s3.Bucket(self, "Site", encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True,
                         block_public_access=s3.BlockPublicAccess.BLOCK_ALL, removal_policy=RemovalPolicy.RETAIN)
        dead = sqs.Queue(self, "FailedReviews", retention_period=Duration.days(14))
        jobs = sqs.Queue(self, "Reviews", visibility_timeout=Duration.minutes(30),
                         dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=2, queue=dead))

        # ---- review worker: scale to zero
        vpc = ec2.Vpc.from_lookup(self, "Vpc", is_default=True)
        # The worker image is built in AWS (CodeBuild), not on your PC: the source is uploaded, CodeBuild
        # runs the Dockerfile and pushes thoughtcoach-worker:latest. Start a build after each deploy
        # (deploy/README.md); the worker always pulls :latest.
        repo = ecr.Repository(self, "WorkerRepo", repository_name="thoughtcoach-worker",
                              removal_policy=RemovalPolicy.RETAIN,
                              lifecycle_rules=[ecr.LifecycleRule(max_image_count=3)])
        source = s3_assets.Asset(self, "Source", path=ROOT, exclude=[
            ".venv", "out", "out_3b", "games", "recordings", "extension", "userscript", "deploy/cdk/cdk.out",
            "**/__pycache__", ".pytest_cache", ".git", "deploy/.upload-key", "**/.venv", "deploy/site"])
        build = codebuild.Project(
            self, "WorkerBuild", project_name="thoughtcoach-worker-build",
            source=codebuild.Source.s3(bucket=source.bucket, path=source.s3_object_key),
            environment=codebuild.BuildEnvironment(build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                                                   privileged=True, compute_type=codebuild.ComputeType.MEDIUM),
            timeout=Duration.minutes(60),
            environment_variables={"REPO": codebuild.BuildEnvironmentVariable(value=repo.repository_uri)},
            build_spec=codebuild.BuildSpec.from_object({
                "version": "0.2",
                "phases": {
                    "pre_build": {"commands": [
                        f"aws ecr get-login-password --region {self.region} | docker login --username AWS "
                        f"--password-stdin {self.account}.dkr.ecr.{self.region}.amazonaws.com"]},
                    "build": {"commands": [r"sed -i 's/\r$//' deploy/worker/entrypoint.sh",
                                           "docker build -f deploy/worker/Dockerfile -t $REPO:latest ."]},
                    "post_build": {"commands": ["docker push $REPO:latest"]},
                }}))
        source.grant_read(build)
        repo.grant_pull_push(build)
        role = iam.Role(self, "WorkerRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                        managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")])
        data.grant_read_write(role)
        site.grant_read_write(role)
        jobs.grant_consume_messages(role)
        repo.grant_pull(role)
        role.add_to_policy(iam.PolicyStatement(actions=["autoscaling:TerminateInstanceInAutoScalingGroup"], resources=["*"]))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "dnf install -y docker && systemctl start docker",
            f"aws ecr get-login-password --region {self.region} | docker login --username AWS --password-stdin "
            f"{self.account}.dkr.ecr.{self.region}.amazonaws.com",
            f"docker run --rm -e TC_QUEUE_URL={jobs.queue_url} -e TC_DATA_BUCKET={data.bucket_name} "
            f"-e TC_SITE_BUCKET={site.bucket_name} -e AWS_DEFAULT_REGION={self.region} {repo.repository_uri}:latest worker",
            "shutdown -h now",  # if the worker stops for any other reason, don't leave the server running
        )
        sg = ec2.SecurityGroup(self, "WorkerSg", vpc=vpc, allow_all_outbound=True, description="no inbound")
        template = ec2.LaunchTemplate(
            self, "WorkerTemplate", instance_type=ec2.InstanceType(WORKER_TYPE),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(), role=role, user_data=user_data,
            security_group=sg, associate_public_ip_address=True,
            instance_initiated_shutdown_behavior=ec2.InstanceInitiatedShutdownBehavior.TERMINATE,
            block_devices=[ec2.BlockDevice(device_name="/dev/xvda",
                                           volume=ec2.BlockDeviceVolume.ebs(40, volume_type=ec2.EbsDeviceVolumeType.GP3))],
            require_imdsv2=True)
        group = autoscaling.AutoScalingGroup(
            self, "Workers", vpc=vpc, launch_template=template, min_capacity=0, max_capacity=1,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC))

        # ---- upload API
        api_fn = lambda_.Function(
            self, "Api", runtime=lambda_.Runtime.PYTHON_3_12, handler="handler.handler",
            code=lambda_.Code.from_asset(os.path.join(ROOT, "deploy", "api")), timeout=Duration.seconds(20),
            memory_size=512, environment={"TC_DATA_BUCKET": data.bucket_name, "TC_SITE_BUCKET": site.bucket_name,
                                          "TC_QUEUE_URL": jobs.queue_url, "TC_WORKER_GROUP": group.auto_scaling_group_name,
                                          "TC_UPLOAD_KEY": upload_key, "TC_DOMAIN": domain or "",
                                          "TC_OWNER": owner or ""})
        data.grant_read_write(api_fn)
        site.grant_read_write(api_fn)
        jobs.grant_send_messages(api_fn)
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["autoscaling:SetDesiredCapacity", "autoscaling:DescribeAutoScalingGroups"], resources=["*"]))
        http = apigw.HttpApi(self, "Http", default_integration=integrations.HttpLambdaIntegration("ApiInt", api_fn))

        # ---- website (pages) + /api through one CloudFront distribution
        cert = zone = None
        if domain:
            zone = route53.HostedZone.from_lookup(self, "Zone", domain_name=domain)
            cert = acm.Certificate(self, "Cert", domain_name=domain,
                                   validation=acm.CertificateValidation.from_dns(zone))
        api_origin = origins.HttpOrigin(f"{http.api_id}.execute-api.{self.region}.amazonaws.com")
        api_behavior = cf.BehaviorOptions(
            origin=api_origin, allowed_methods=cf.AllowedMethods.ALLOW_ALL, cache_policy=cf.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            viewer_protocol_policy=cf.ViewerProtocolPolicy.HTTPS_ONLY)
        rewrite = cf.Function(self, "Rewrite", code=cf.FunctionCode.from_inline(REWRITE))
        dist = cf.Distribution(
            self, "Web", default_root_object="index.html",
            domain_names=[domain] if domain else None, certificate=cert,
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                function_associations=[cf.FunctionAssociation(function=rewrite, event_type=cf.FunctionEventType.VIEWER_REQUEST)]),
            additional_behaviors={"/api/*": api_behavior, "/health": api_behavior})
        # The landing and setup pages, and the extension download. Never removes anything else
        # (the players' review pages live in the same bucket).
        build_extension_zip()
        s3_deploy.BucketDeployment(
            self, "SitePages", destination_bucket=site, sources=[s3_deploy.Source.asset(SITE_DIR)], prune=False,
            cache_control=[s3_deploy.CacheControl.from_string("no-cache")], distribution=dist,
            distribution_paths=["/", "/index.html", "/start*", "/privacy*", "/live*", "/site.css", "/favicon.png", "/thoughtcoach-extension.zip"])
        if zone:
            route53.ARecord(self, "Alias", zone=zone, target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(dist)))

        cdk.CfnOutput(self, "Url", value=f"https://{domain or dist.distribution_domain_name}")
        cdk.CfnOutput(self, "WorkerGroup", value=group.auto_scaling_group_name)
        cdk.CfnOutput(self, "WorkerBuildProject", value=build.project_name)
        cdk.CfnOutput(self, "DataBucket", value=data.bucket_name)
        cdk.CfnOutput(self, "SiteBucket", value=site.bucket_name)


app = cdk.App()
key = app.node.try_get_context("upload_key")
if not key:
    raise SystemExit("pass the upload key: -c upload_key=<secret> (see deploy/README.md)")
ThoughtCoach(app, "ThoughtCoach", domain=app.node.try_get_context("domain"), upload_key=key,
             owner=app.node.try_get_context("owner"),
             env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-east-1"))
app.synth()
