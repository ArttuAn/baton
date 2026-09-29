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
