"""The PKGBUILD reader itself, because every naive one of these is wrong here.

The parser this replaces truncated mid-array on a `)` inside a comment, which
turned `whisper-cpp` from *declared* into *absent* and produced a test failure
reading **"a default install has no speech input"** about a manifest that
declares it in plain sight. A parser that stops in the wrong place is worse than
no parser: it answers confidently and wrongly.

Each test here has a control that can fail - the naive parse is asserted to be
wrong, not merely replaced.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _pkgbuild as pb  # noqa: E402


#: The real shape that broke it: a `)` inside a comment mid-array.
TRUNCATING = """depends=(
    'python'
    # expand, factor, prime_factors, linear solve) and bc runs the
    # exact rational arithmetic the kernel cannot do
    'bc'
    'whisper-cpp'
)
"""


def test_a_paren_inside_a_comment_does_not_end_the_array():
    assert pb.depends(TRUNCATING) == ["python", "bc", "whisper-cpp"]


def test_the_naive_split_is_the_thing_that_was_wrong():
    """The control. If this ever stops being true the parser is no longer
    earning its existence - and if it stops raising, the test above has stopped
    testing anything."""
    naive = TRUNCATING.split("depends=(", 1)[1].split(")", 1)[0]
    assert "whisper-cpp" not in naive, (
        "the naive split no longer truncates here, so this file no longer "
        "demonstrates the failure it exists to prevent")
    assert "bc" not in naive, "the naive split stops before the entry after the comment"


def test_a_comment_after_an_entry_is_not_a_dependency():
    assert pb.depends("""depends=(
    'whisper-cpp' # whisper-cpp: voice input (speech recognition)
    'bc'
)
""") == ["whisper-cpp", "bc"]


def test_optdepends_is_never_mistaken_for_a_hard_dependency():
    """The distinction several tests actually ask: `whisper-cpp` as a hard
    dependency is correct, and the same string in `optdepends` would tell a
    user to install what they already have."""
    text = """depends=(
    'whisper-cpp'
)
optdepends=(
    'python-polars: learning.py reads tool_calls.log into dataframes'
    'ollama: an alternative local model server'
)
"""
    assert pb.depends(text) == ["whisper-cpp"]
    assert pb.optdepends(text) == ["python-polars", "ollama"]
    assert not pb.package_missing("whisper-cpp", text)
    assert pb.package_missing("python-polars", text)


def test_an_empty_array_is_empty_not_an_error():
    assert pb.depends("optdepends=()\ndepends=('python')\n") == ["python"]
    assert pb.optdepends("depends=('python')\n") == []


def test_a_missing_array_reads_as_empty_rather_than_raising():
    assert pb.depends("# no dependencies here\npkgver=1\n") == []


def test_an_unquoted_entry_is_still_read():
    """Arch allows one unquoted word; a parser that requires quotes reports the
    dependency as absent, which is the same confident wrong answer."""
    assert pb.array("depends=(python)\n", "depends") == ["python"]


def test_the_shipping_manifest_is_where_the_helper_says_it_is():
    """Not that the file exists - that the path is *constructed*, so a caller
    cannot invent one and land on the deleted in-repo copy."""
    path = pb.arch_pkgbuild()
    assert path.name == "PKGBUILD"
    assert path.parent.name == "shani-chronoa"
    assert path.exists()


def test_the_real_manifest_declares_what_the_package_needs_to_work():
    """The regression this whole file exists for, against the real file rather
    than a fixture."""
    depends = pb.depends(pb.arch_pkgbuild().read_text(encoding="utf-8"))
    # Both engines the maths skill refuses without.
    assert "python-symengine" in depends and "bc" in depends
    assert "python-sympy" not in depends, "sympy was replaced by symengine"
    # Speech in, speech out, the local model.
    assert "whisper-cpp" in depends
    assert "llama-cpp" in depends
    assert "espeak-ng" in depends
    assert "tesseract" in depends


def test_the_real_manifests_comment_does_not_hide_a_later_dependency():
    """`bc` is declared after the comment containing a `)`. If a reader ever
    truncates there again, `bc` is the first casualty and maths silently loses
    its arithmetic engine."""
    text = pb.arch_pkgbuild().read_text(encoding="utf-8")
    depends = pb.depends(text)
    assert text.index("linear solve)") < text.index("'bc'")
    assert "bc" in depends