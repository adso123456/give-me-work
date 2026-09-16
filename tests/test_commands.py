from commands import (
    parse_command_args,
    parse_confirm_args,
    parse_delete_args,
    parse_find_args,
)


def test_parse_command_args_accepts_wake_prefix_variants():
    assert parse_command_args("/job_delete rec1", "job_delete") == ["rec1"]
    assert parse_command_args("!job_delete rec1 rec2", "job_delete") == ["rec1", "rec2"]
    assert parse_command_args("job_delete", "job_delete") == []
    assert parse_command_args("/job_delete", "job_delete") == []
    assert parse_command_args("  /job_delete   rec1   ", "job_delete") == ["rec1"]


def test_parse_command_args_rejects_other_commands():
    # 关键：/job_delete_confirm 不能被当成 /job_delete
    assert parse_command_args("/job_delete_confirm abc", "job_delete") is None
    assert parse_command_args("/job_status", "job_delete") is None
    assert parse_command_args("随便聊聊", "job_delete") is None
    assert parse_command_args("", "job_delete") is None
    assert parse_command_args("/xjob_delete rec1", "job_delete") is None


def test_parse_delete_args_flags_force():
    assert parse_delete_args("/job_delete rec1") == (["rec1"], False)
    assert parse_delete_args("/job_delete rec1 --yes") == (["rec1"], True)
    assert parse_delete_args("/job_delete rec1 rec2 -y") == (["rec1", "rec2"], True)
    assert parse_delete_args("/job_delete --yes") == ([], True)
    assert parse_delete_args("/job_delete") == ([], False)
    assert parse_delete_args("/job_delete_confirm tok") is None


def test_parse_confirm_and_find_args():
    assert parse_confirm_args("/job_delete_confirm tok123") == ["tok123"]
    assert parse_confirm_args("/job_delete tok123") is None
    assert parse_find_args("/job_delete_find 字节 跳动") == ["字节", "跳动"]
    assert parse_find_args("/job_delete_find") == []
    assert parse_find_args("/job_delete 字节") is None
