RETRY_TRIES = 5
RETRY_DELAY = 3


class PackageNotInstalled(Exception):
    pass


def retry_sleep(trial):
    # First trial
    if trial == 0:
        return 0
    # Failed once, fast retry
    elif trial == 1:
        return 0
    # Failed twice
    elif trial == 2:
        return 1
    # Failed more
    else:
        return RETRY_DELAY
