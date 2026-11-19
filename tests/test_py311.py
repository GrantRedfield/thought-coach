"""The review server runs Python 3.11: f-strings must not use what only 3.12+ accepts (a backslash
inside the braces, or the f-string's own quote reused inside them). Tests here run on a newer Python,
so this catches it before a deploy does."""
import io
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(sys.version_info < (3, 12), reason="f-string tokens need Python 3.12+ to inspect")
def test_f_strings_work_on_python_311():
    problems = []
    for path in sorted((ROOT / "thoughtcoach").glob("*.py")):
        stack = []
        for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
            name = tokenize.tok_name[tok.type]
            if name == "FSTRING_START":
                body = tok.string.lstrip("rRfFbBuU")
                quote = body[:3] if body[:3] in ('"""', "'''") else body[0]
                if stack and stack[-1] in tok.string:
                    problems.append(f"{path.name}:{tok.start[0]} nested f-string reuses its quote")
                stack.append(quote)
            elif name == "FSTRING_END":
                stack.pop()
            elif stack and name != "FSTRING_MIDDLE":
                if "\\" in tok.string:
                    problems.append(f"{path.name}:{tok.start[0]} backslash inside f-string braces")
                if name == "STRING" and stack[-1] in tok.string[:4]:
                    problems.append(f"{path.name}:{tok.start[0]} string inside f-string braces reuses its quote")
    assert not problems, problems
