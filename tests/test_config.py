from config import TestingConfig as _TestingConfig


def test_testing_config_disables_process_local_background_tasks():
    """Temporary test databases must not share process-local worker threads."""

    assert _TestingConfig.ASYNC_TASKS_ENABLED is False
    assert _TestingConfig.PRESET_SCAN_ENABLED is False
