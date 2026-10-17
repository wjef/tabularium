import pytest

from tabularium import paths
from tabularium.lock import AlreadyRunningError, InstanceLock


def test_second_instance_is_refused():
    with InstanceLock(paths.lock_file()), pytest.raises(AlreadyRunningError):
        InstanceLock(paths.lock_file()).acquire()


def test_lock_can_be_reacquired_after_release():
    with InstanceLock(paths.lock_file()):
        pass
    with InstanceLock(paths.lock_file()):
        pass
