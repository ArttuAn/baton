import subprocess

from baton import picker

WMCTRL = (
    "0x046aa60a  0 Arttus ✳ AI-native router design\n"
    "0x046b5fad  0 Arttus baton-pick #340778\n"
    "0x02a00003 -1 Arttus \n"
)


def test_titles_keep_every_word_and_normalise_the_window_id(monkeypatch):
    done = subprocess.CompletedProcess([], 0, stdout=WMCTRL)
    monkeypatch.setattr(picker.subprocess, "run", lambda *a, **k: done)
    titles = picker._titles()
    assert titles["0x46aa60a"] == "✳ AI-native router design"
    assert titles["0x2a00003"] == ""
    assert picker._norm("0x046b5fad") == "0x46b5fad"


def test_a_probed_window_maps_to_the_pid_that_retitled_it(monkeypatch):
    done = subprocess.CompletedProcess([], 0, stdout=WMCTRL)
    monkeypatch.setattr(picker.subprocess, "run", lambda *a, **k: done)
    assert picker._mapped() == {"0x46b5fad": 340778}


def test_restoring_titles_survives_a_stale_probe_from_a_crashed_run(monkeypatch):
    """A leftover 'baton-pick' title from a crashed run must not crash the restore."""
    monkeypatch.setattr(picker, "_require_x11", lambda: None)
    monkeypatch.setattr(picker, "_agents", lambda: [(1234, "claude")])
    monkeypatch.setattr(picker, "_ancestors", lambda: set())
    monkeypatch.setattr(picker, "_tty", lambda pid: "/dev/pts/7")
    monkeypatch.setattr(picker, "_click", lambda timeout: "0xown")
    monkeypatch.setattr(picker.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        picker, "_resolve", lambda pid, harness, title: picker.Picked(harness, pid, "/w", None, title)
    )

    restored: list[tuple[str, str]] = []
    monkeypatch.setattr(picker, "_retitle", lambda tty, title: restored.append((tty, title)))

    # read 0 is `before`; reads 1-5 are _mapped() during the probe rounds, after
    # the click, and in the finally block. 0xstale carries a dead pid (999999)
    # the way a crashed previous run would leave behind.
    probed = {"0xown": "baton-pick #1234", "0xstale": "baton-pick #999999"}
    reads = [{"0xown": "agent title", "0xstale": "old title"}, *(probed,) * 5]
    monkeypatch.setattr(picker, "_titles", lambda: reads.pop(0))

    picked = picker.pick(timeout=1)
    assert picked.pid == 1234
    # our probe went out, and only our own window was restored —
    # the stale pid (999999) was skipped instead of raising KeyError
    assert restored == [("/dev/pts/7", "baton-pick #1234"), ("/dev/pts/7", "agent title")]
