import json
import sqlite3

from baton.model import SessionRef
from baton.readers import claude, opencode


def write_transcript(tmp_path, entries):
    folder = tmp_path / "projects" / claude.slug("/work/proj")
    folder.mkdir(parents=True)
    file = folder / "abc123.jsonl"
    file.write_text("\n".join(json.dumps(entry) for entry in entries))
    return file


def test_claude_reader_keeps_typed_prompts_and_drops_injected_text(tmp_path, monkeypatch):
    file = write_transcript(
        tmp_path,
        [
            {"type": "user", "cwd": "/work/proj", "timestamp": "t0", "origin": {"kind": "human"},
             "message": {"role": "user", "content": "do the work"}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>ignore me</system-reminder>"}]}},
            {"type": "assistant", "timestamp": "t1", "message": {"role": "assistant", "model": "opus",
             "content": [
                {"type": "text", "text": "on it"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}},
             ]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True}]}},
        ],
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    refs = claude.list_sessions("/work/proj")
    assert [ref.id for ref in refs] == ["abc123"]

    session = claude.load(SessionRef(harness="claude", id="abc123", source=str(file)))
    assert [turn.text for turn in session.human_prompts()] == ["do the work"]
    assert session.model == "opus"
    call = session.tool_calls()[0]
    assert (call.kind, call.command, call.ok) == ("run", "pytest", False)


def test_claude_slug_matches_the_projects_folder_layout():
    assert claude.slug("/home/arttu/proj") == "-home-arttu-proj"


def build_opencode_db(path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        create table session (id text, project_id text, workspace_id text, parent_id text, slug text,
          directory text, path text, title text, version text, share_url text, summary_additions int,
          summary_deletions int, summary_files int, summary_diffs text, metadata text, cost real,
          tokens_input int, tokens_output int, tokens_reasoning int, tokens_cache_read int,
          tokens_cache_write int, revert text, permission text, agent text, model text,
          time_created int, time_updated int, time_compacting int, time_archived int);
        create table message (id text, session_id text, time_created int, time_updated int, data text);
        create table part (id text, message_id text, session_id text, time_created int,
          time_updated int, data text);
        """
    )
    db.execute(
        "insert into session (id, directory, title, model, time_created, time_updated)"
        " values ('ses_1', '/work/proj', 'Fix the parser', ?, 1000, 2000)",
        (json.dumps({"providerID": "opencode", "modelID": "big-pickle"}),),
    )
    db.execute(
        "insert into message (id, session_id, time_created, data) values ('msg_1','ses_1',1000,?)",
        (json.dumps({"role": "user", "time": {"created": 1000}}),),
    )
    db.execute(
        "insert into part (id, message_id, session_id, time_created, data) values ('p1','msg_1','ses_1',1,?)",
        (json.dumps({"type": "text", "text": "fix the parser"}),),
    )
    db.execute(
        "insert into part (id, message_id, session_id, time_created, data) values ('p2','msg_1','ses_1',2,?)",
        (json.dumps({"type": "tool", "tool": "edit",
                     "state": {"status": "completed", "input": {"filePath": "src/p.py"}}}),),
    )
    db.commit()
    db.close()


def test_opencode_reader_joins_messages_to_their_parts(tmp_path, monkeypatch):
    data = tmp_path / "opencode"
    data.mkdir()
    build_opencode_db(data / "opencode.db")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    refs = opencode.list_sessions("/work/proj")
    assert [(ref.id, ref.title) for ref in refs] == [("ses_1", "Fix the parser")]

    session = opencode.load(refs[0])
    assert session.model == "opencode/big-pickle"
    assert session.turns[0].text == "fix the parser"
    assert session.tool_calls()[0].path == "src/p.py"


def test_opencode_reader_filters_by_project_directory(tmp_path, monkeypatch):
    data = tmp_path / "opencode"
    data.mkdir()
    build_opencode_db(data / "opencode.db")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert opencode.list_sessions("/somewhere/else") == []


def test_readers_return_nothing_when_a_store_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
    assert opencode.list_sessions() == []
    assert claude.list_sessions() == []
