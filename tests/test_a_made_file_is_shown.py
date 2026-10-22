"""A file a skill made is shown, not described - and only if it *declared* it.

The claim this file exists to hold: **the transcript renders a path only when
the skill's own code says it wrote that path.**

Before this, `generate_image` ended its answer with *"saved it to
/home/you/pictures/cat.png"* and the transcript rendered that sentence. A picture
the assistant had just made was a line of text, and seeing it meant leaving the
app for a file manager.

The obvious cheap version of this feature is to scrape the path out of the
sentence. **That is the thing being refused**, and it is refused for a reason
this repository already has a name for: `result` is prose the model wrote and
reads, so a path inside it may have come from a web page the model quoted rather
than from anything on this machine. Rendering a file on the strength of one is a
prompt-injection surface wearing a thumbnail.

So a skill **declares** what it wrote (`DECLARES_ARTIFACTS`), `tools` collects
that lazily, and only an absolute path that is on disk becomes an artifact.
"""

import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import tools, verification  # noqa: E402
from shani_chronoa.gui import blocks  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def a_real_file(tmp_path):
    path = tmp_path / "picture.png"
    path.write_bytes(PNG)
    return path


# --------------------------------------------------------------------------
# the field, and that adding it broke nothing


def test_the_field_exists_and_defaults_to_nothing():
    result = tools.DispatchResult("t", verification.Verdict.UNVERIFIED, False)
    assert result.artifacts == (), (
        "a dispatch that declared nothing must report nothing, or every skill "
        "renders an artifact nobody asked for")


def test_every_existing_construction_shape_still_works():
    """**The non-breaking claim, asserted rather than argued.**

    All thirteen construction sites in the tree build the first three arguments
    positionally, so a new fifth field must not disturb them. `evidence`
    already carried a default, which is what makes that true.
    """
    positional = tools.DispatchResult("text", verification.Verdict.FAILED, True)
    assert positional.evidence == ""
    assert positional.artifacts == ()
    assert len(positional) == len(tools.DispatchResult._fields)


# --------------------------------------------------------------------------
# the trust property: only what a skill declared


def test_a_skill_declares_what_it_wrote(a_real_file):
    assert tools.artifacts_for(
        "generate_image",
        f"Made a 512x512 picture in 12 s and saved it to {a_real_file}."
    ) == (a_real_file,)


def test_a_path_in_prose_the_skill_did_not_write_declares_nothing(tmp_path):
    """**The load-bearing gate.**

    A sentence full of paths that this skill did not produce must declare
    **nothing**. If this fails, the feature is a regex and every reply that
    mentions a file can put one on screen.
    """
    quoted = tmp_path / "quoted-from-a-web-page.png"
    quoted.write_bytes(PNG)
    for sentence in (
        f"the docs at {quoted} explain it",
        f"see {quoted} and /etc/passwd",
        f"not written: {quoted}",
        f"error: {quoted}",
    ):
        assert tools.artifacts_for("generate_image", sentence) == (), (
            f"a path the skill did not write became an artifact: {sentence!r}")


def test_a_file_that_is_not_there_is_not_an_artifact(tmp_path):
    """A skill can be wrong about a file it meant to write."""
    gone = tmp_path / "never-written.png"
    assert tools.artifacts_for(
        "generate_image", f"saved it to {gone}.") == ()


def test_a_relative_path_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "here.png").write_bytes(PNG)
    assert tools.artifacts_for(
        "generate_image", "saved it to here.png.") == (), (
        "a relative path would resolve against whatever directory the UI "
        "happened to be in")


def test_a_refusal_declares_nothing(a_real_file):
    """Every way `_run` can decline, checked together - they are three sentences
    and only one is actionable."""
    for sentence in (
        "Say what the picture should show.",
        "Making pictures is not set up on this computer.",
        f"Could not make the picture: no engine, though it tried {a_real_file}",
    ):
        assert tools.artifacts_for("generate_image", sentence) == ()


def test_a_declaration_that_raises_contributes_nothing(a_real_file):
    """**An artifact declaration may never break a tool call.**"""
    tools.ARTIFACTS["explodes"] = lambda _text: 1 / 0
    try:
        assert tools.artifacts_for("explodes", f"saved it to {a_real_file}.") == ()
    finally:
        tools.ARTIFACTS.pop("explodes", None)


def test_an_undeclared_skill_declares_nothing(a_real_file):
    assert tools.artifacts_for("get_datetime",
                               f"saved it to {a_real_file}.") == ()


# --------------------------------------------------------------------------
# the block


def _widgets(node):
    out, stack = [], [node]
    while stack:
        current = stack.pop()
        child = current.get_first_child() if isinstance(current, Gtk.Widget) else None
        while child is not None:
            out.append(child)
            stack.append(child)
            child = child.get_next_sibling()
    return out


def _buttons(root):
    return [w for w in _widgets(root) if isinstance(w, Gtk.Button)]


def _images_in(root):
    """`Gtk.Image`s that are not inside a control - i.e. thumbnails only."""
    out = []
    for w in _widgets(root):
        if not isinstance(w, Gtk.Image):
            continue
        parent = w.get_parent()
        if isinstance(parent, Gtk.Button):
            continue                    # a control's own icon
        out.append(w)
    return out


def _thumbnail_of(block):
    """The row's **first child**, which is where the thumbnail goes.

    Not "every `Gtk.Image` in the tree": each of the three controls is a
    `_flat_button`, and a flat button *is* an `Gtk.Image` inside a `Gtk.Button`.
    The first version counted those and concluded the broken image had a
    thumbnail - a test measuring the wrong thing, which is the same defect as
    the witness that could not fail.
    """
    row = block.get_last_child()
    if row is None:
        return None
    first = row.get_first_child()
    # A *thumbnail* is a rendered picture (a paintable). The row's first child is
    # now always an image - a type icon when there is no thumbnail - so a type
    # icon (drawn from an icon name, no paintable) is not counted as one here.
    if isinstance(first, Gtk.Image) and first.get_paintable() is not None:
        return first
    return None


#: The controls an artifact row is expected to carry. **Asserted as a set, not
#: as a count.** A bare `== 3` went stale the moment preview was added and
#: started failing for no reason a reader could see; a set names what the row is
#: *for*, so adding a control is a deliberate edit to this line rather than a
#: silent breakage somewhere else.
ARTIFACT_CONTROLS = ("Open ", "Copy this file's path", "Save a copy elsewhere",
                     "Preview ")


def _control_names(block):
    return [b.get_tooltip_text() for b in _walk(block)
            if isinstance(b, Gtk.Button) and b.get_tooltip_text()]


def test_a_made_file_is_shown_with_its_four_controls(a_real_file):
    block = blocks.ArtifactBlock([a_real_file])
    names = _control_names(block)
    # A made file is openable, its path is copyable, a copy is saveable and it
    # can be previewed - the controls CodeBlock already has for code.
    assert all(any(n.startswith(c) for n in names) for c in ARTIFACT_CONTROLS), (
        f"missing a control, got {names}")
    labels = [w for w in _widgets(block) if isinstance(w, Gtk.Label)]
    assert any(a_real_file.name in (w.get_label() or "") for w in labels)


def test_a_real_image_gets_a_thumbnail_and_a_broken_one_does_not(a_real_file,
                                                                tmp_path):
    """**No placeholder on failure.** A grey box standing in for a picture
    reads as "this is the picture" and is not one."""
    broken = tmp_path / "not-really.png"
    broken.write_bytes(PNG)                 # right extension, not a real image
    assert _thumbnail_of(blocks.ArtifactBlock([a_real_file])) is None
    broken_names = _control_names(blocks.ArtifactBlock([broken]))
    assert all(any(n.startswith(c) for n in broken_names)
               for c in ARTIFACT_CONTROLS), (
        "a file with no thumbnail must still be openable and copyable - the "
        "name is what a person can act on")


def test_a_thumbnail_actually_renders(tmp_path):
    """The other half: that a valid image produces a thumbnail at all.

    **This is the control for the test above.** `ArtifactBlock._thumbnail`
    caught its own `TypeError` and logged it while the block silently carried
    on with no picture, so "no broken image gets a placeholder" passed
    perfectly while the working path was dead. A control that only proves a
    thing is missing proves nothing about a thing that is present.
    """
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except (ImportError, ValueError):                      # pragma: no cover
        pytest.skip("GdkPixbuf is not usable here")
    real = tmp_path / "real.png"
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 32, 32)
    pixbuf.fill(0x3366CCFF)
    assert pixbuf.savev(str(real), "png", [], []) is True
    assert _thumbnail_of(blocks.ArtifactBlock([real])) is not None, (
        "a valid image produced no thumbnail - the fallback is hiding a broken "
        "primary path, which is how a dead control reads as working")


def test_the_card_renders_artifacts_and_shows_nothing_without_them(a_real_file):
    """**Asserted on `ArtifactBlock` itself**, not on "no images".

    Two earlier versions of this asserted on images and both were wrong: a
    `_flat_button` *is* an `Gtk.Image` inside a `Gtk.Button`, and the card's
    own success/failure icon is a direct `Gtk.Image` child. Both made a
    correct card look wrong. The claim is about the block, so the block is what
    is asked about.
    """
    def _blocks_in(root):
        return [w for w in _widgets(root) if isinstance(w, blocks.ArtifactBlock)]

    card = blocks.ToolCallCard("generate_image", {"prompt": "a cat"},
                               "Made a picture.", True, artifacts=(a_real_file,))
    made = _blocks_in(card)
    assert len(made) == 1, f"a declared artifact rendered no file row: {_blocks_in(card)}"
    made_names = _control_names(made[0])
    assert all(any(n.startswith(c) for n in made_names) for c in ARTIFACT_CONTROLS), (
        f"the declared artifact's row lost a control: {made_names}")

    plain = blocks.ToolCallCard("get_datetime", {}, "It is noon.", True)
    assert not _blocks_in(plain), (
        "a card with no artifacts must show no file row at all")


# --------------------------------------------------------------------------
# the sink, which had no arity check until this widening needed one


def test_the_sink_refuses_a_subscriber_of_the_wrong_arity():
    """**The arity check, and why it exists.**"""
    from shani_chronoa.events import EventSink

    def four(_n, _a, _r, _ok):               # the pre-widening shape
        pass

    def five(_n, _a, _r, _ok, _artifacts):
        pass

    with pytest.raises(TypeError, match="5 arguments"):
        EventSink(on_tool_finish=four)
    assert EventSink(on_tool_finish=five) is not None


def test_a_star_args_subscriber_is_not_a_mismatch():
    """**The correction.** The first version refused `*args` subscribers, which
    broke two tests that build one on purpose - `test_a_callable_object_builds`
    exists precisely to prove the guard is not `isinstance(value, type(lambda:
    None))`, and a sink that raises is given `explodes(*_args)`.

    A `*args` callable accepts any arity, so refusing it was the guard
    rejecting correct code.
    """
    from shani_chronoa.events import EventSink

    class _Callable:
        def __call__(self, *args):
            return args

    assert EventSink(on_tool_start=_Callable()) is not None
    assert EventSink(on_tool_finish=lambda *a: None) is not None

# --------------------------------------------------------------------------
# the channel: a skill is a subprocess, so the answer string is all it has


def test_the_tool_outcome_carries_artifacts(tmp_path):
    """**The field whose absence made the whole feature deliver nothing.**

    The plumbing was complete and correct at every hop: `DispatchResult` carried
    `artifacts`, the assistant read `result.artifacts`, the transcript built an
    artifact block. It delivered nothing because `_run_tool` returns a
    `ToolOutcome`, which had no such field, so `getattr(result, "artifacts", ())`
    answered `()` - quietly, at runtime, with no error anywhere. Found by
    running a real `create_document` and printing the field.
    """
    made = tmp_path / "made.md"
    made.write_text("# hi\n", encoding="utf-8")

    outcome = tools.ToolOutcome(text="Saved made.md.", verdict=verification.Verdict.UNVERIFIED,
                                artifacts=(made,))
    assert outcome.artifacts == (made,)


def test_a_dispatched_skill_that_makes_a_file_declares_it(tmp_path):
    """End to end, through the real dispatcher: write a document, get it back."""
    target = tmp_path / "notes.md"
    result = tools.execute_tool_outcome(
        "create_document",
        {"title": "Chronoa", "body": "A real document.", "format": "md",
         "path": str(target)})

    assert target.exists(), f"the skill did not write it: {result.text}"
    assert result.artifacts == (target,), (
        f"wrote {target} but declared {result.artifacts} - {result.text}")


# --------------------------------------------------------------------------
# the shared extractor, and the skills that use it


def test_paths_in_returns_the_captured_group():
    from shani_chronoa import files
    find = files.paths_in(r"saved it to (.+?)\.$")
    assert find("it took 4 s and saved it to /tmp/a.png.") == ("/tmp/a.png",)


def test_paths_in_returns_nothing_for_a_foreign_sentence():
    """**The negative control, and it has to be a real failure.**

    The risk in a shared extractor is a pattern loose enough to match prose the
    skill never wrote - a path quoted out of a web page, a refusal that repeated
    one back. If this passed for the wrong reason the feature would be the
    prompt-injection channel it exists to prevent, so the negative control has
    to be a shape the pattern genuinely must reject.
    """
    from shani_chronoa import files
    find = files.paths_in(r"^Saved (.+?) \(\d+ bytes\)\.$")
    assert find("I could not write anything.") == ()
    assert find("Saved /tmp/a.md (not a number).") == ()
    assert find("A page said: Saved /tmp/from-the-web.md (12 bytes).") == ()


def test_paths_in_tolerates_a_missing_or_non_string_answer():
    """A declaration that raises would be logged on every odd answer, which is
    how a log gets trained to be ignored."""
    from shani_chronoa import files
    find = files.paths_in(r"saved it to (.+?)\.$")
    assert find(None) == ()
    assert find("") == ()
    assert find(12345) == ()


def test_a_declared_file_that_is_not_on_disk_is_dropped(tmp_path):
    """The declaration narrows the sentence; `artifacts_for` is the check."""
    from shani_chronoa import files
    tools.declare_artifacts("claims_a_file", files.paths_in(r"Saved (.+?)\.$"))
    try:
        missing = tmp_path / "never-written.md"
        # The pattern matches - the sentence is exactly this skill's own wording.
        assert tools.artifacts_for("claims_a_file", f"Saved {missing}.") == ()
    finally:
        tools.ARTIFACTS.pop("claims_a_file", None)


def test_the_skills_that_made_a_file_all_declare_one():
    """**Every skill whose answer names a full path declares it.**

    Checked against the real skill modules rather than a hand-written list, so a
    skill gaining the declaration does not need this test edited - and so a
    skill *losing* it is caught here instead of by a person noticing a
    thumbnail stopped appearing.
    """
    from shani_chronoa import tools
    declaring = {n for n in tools._HANDLER_FNS if tools._declaration_for(n)}
    expected = {"generate_image", "create_document", "convert_document",
                "take_photo", "analyze_table", "recording", "edit_image",
                "office_document"}
    assert expected <= declaring, f"no longer declaring: {sorted(expected - declaring)}"


def _walk(widget):
    stack, out = [widget], []
    while stack:
        node = stack.pop(0)
        out.append(node)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return out


def _sample_colours(path, size=8):
    """Read a PNG back into a set of the colours it contains.

    Decoded with GdkPixbuf rather than a hand-written decoder: the test needs
    *which colours are in this picture*, and a PNG undecoder written for that
    would be thirty lines of code whose own bugs would be indistinguishable from
    a broken thumbnail.
    """
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
    data, channels, stride = pixbuf.get_pixels(), pixbuf.get_n_channels(), pixbuf.get_rowstride()
    width, height = pixbuf.get_width(), pixbuf.get_height()
    seen = set()
    for y in range(0, height, size):
        for x in range(0, width, size):
            at = y * stride + x * channels
            seen.add((data[at], data[at + 1], data[at + 2]))
    return seen


def test_the_thumbnail_holds_the_picture_not_just_an_image_widget(tmp_path):
    """**The test above checks that a thumbnail widget exists. This checks the
    pixels inside it.**

    Those are different claims, and the gap between them is exactly the failure
    this repository keeps paying for: `_thumbnail` catches its own `TypeError`
    and falls back to no preview at all, so *any* assertion weaker than "the
    right colours are in there" passes just as happily over a dead primary path.
    An empty texture is still a texture, and `Gdk.Texture` happily hands out a
    96x96 blank one.

    The source is four solid quadrants of four distinct colours, so "the image
    arrived" is one measurement rather than four guesses about the same pixels -
    and a uniform grey box, which is what a silently-broken preview looks like,
    contains exactly one colour and fails all four.
    """
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    try:
        GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1, 1)
    except Exception:                                     # noqa: BLE001
        pytest.skip("GdkPixbuf is not usable here")

    size, half = 128, 64
    quadrant_colours = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
    rows = bytearray()
    for y in range(size):
        for x in range(size):
            rows += bytes(quadrant_colours[(0 if x < half else 1) + (0 if y < half else 2)])
    source = tmp_path / "quadrants.png"
    pixbuf = GdkPixbuf.Pixbuf.new_from_data(
        bytes(rows), GdkPixbuf.Colorspace.RGB, False, 8, size, size, size * 3)
    assert pixbuf.savev(str(source), "png", [], []) is True

    image = _thumbnail_of(blocks.ArtifactBlock([source]))
    assert image is not None, "no thumbnail at all"
    paintable = image.get_paintable()
    assert hasattr(paintable, "save_to_png"), (
        f"the thumbnail is a {type(paintable).__name__}, which cannot be sampled")

    saved = tmp_path / "thumbnail.png"
    paintable.save_to_png(str(saved))
    seen = _sample_colours(saved)

    missing = [c for c in quadrant_colours if c not in seen]
    assert not missing, (
        f"the thumbnail does not contain {missing} - it holds {sorted(seen)}, so "
        f"the preview is not the picture that was made")


def test_a_declared_skill_really_declares_what_it_says_it_saved(tmp_path):
    """**A declaration is a regex over the skill's own sentence, so editing the
    sentence silently undeclares the skill - and no structural test sees it.**

    That is not hypothetical. `edit_image` reported "0 KB, was 0 KB" for
    anything under 512 bytes, so it was changed to report a real size; its
    `DECLARES_ARTIFACTS` still existed, so the test asserting that six skills
    declare one kept passing, and the transcript had quietly gone back to
    describing files instead of showing them. Found by running the skill and
    printing what it declared - the declaration was `[]`.

    So the gate is not "does the attribute exist" but "does running the skill
    produce an artifact". Two skills here are cheap and self-contained; a
    heavier one would need its binary, which is why the set is explicit rather
    than every declaring skill.
    """
    source = tmp_path / "cat.png"
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    buf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 64)
    buf.fill(0x804020FF)
    assert buf.savev(str(source), "png", [], []) is True

    result = tools.execute_tool_outcome(
        "edit_image", {"path": str(source), "look": "sepia"})

    assert result.artifacts, (
        f"edit_image ran and said {result.text[:70]!r} but declared nothing - "
        f"its DECLARES_ARTIFACTS no longer matches its own sentence")
    for path in result.artifacts:
        assert path.exists(), f"declared {path}, which is not on disk"


def test_a_directory_artifact_does_not_offer_a_save_it_cannot_do(tmp_path):
    """A folder is a legitimate artifact - `office_document` writes a directory
    of extracted page images - but `_saved` reads its source with `read_bytes()`,
    which raises `IsADirectoryError`. That is caught and logged, so the button
    would fail silently: a control that does nothing and says nothing.

    Measured by rendering a real directory rather than by reading the code, which
    is the only reason the wording of that log line is known.
    """
    folder = tmp_path / "page-images"
    folder.mkdir()
    (folder / "image-001.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    block = blocks.ArtifactBlock([folder])
    tooltips = [b.get_tooltip_text() for b in _walk(block)
                if isinstance(b, Gtk.Button)]

    assert tooltips, "a directory artifact rendered no controls at all"
    assert not any("Save a copy" in (t or "") for t in tooltips), (
        f"a folder still offers save-a-copy: {tooltips}")
    assert any("Open" in (t or "") for t in tooltips), (
        f"a folder lost its open control: {tooltips}")


def test_a_file_artifact_offers_every_control(tmp_path):
    """The control for the directory test above: dropping one must not drop
    them all. A set rather than a count, for the reason on ARTIFACT_CONTROLS."""
    made = tmp_path / "cat-edited.png"
    made.write_bytes(b"\x89PNG\r\n\x1a\n")
    names = _control_names(blocks.ArtifactBlock([made]))
    missing = [c for c in ARTIFACT_CONTROLS
               if not any(n.startswith(c) for n in names)]
    assert not missing, f"a file lost {missing}; it has {names}"
