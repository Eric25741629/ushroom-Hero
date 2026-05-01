from task_sandbox.cli import build_parser, run_list


def test_list_subcommand_outputs_registered_tasks(capsys):
    args = build_parser().parse_args(["list"])
    run_list(args)
    out = capsys.readouterr().out
    assert "lamp" in out
