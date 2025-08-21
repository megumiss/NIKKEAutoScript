import os
import random
import string


def random_id():
    """
    Returns:
        str: Random ID, like "sTD2kF"
    """
    # 6 random letter (62^6 combinations) would be enough
    return ''.join(random.sample(string.ascii_letters + string.digits, 6))

def is_tmp_file(file: str) -> bool:
    """
    Check if a filename is tmp file
    """
    # Check suffix first to reduce regex calls
    if not file.endswith('.tmp'):
        return False
    # Check temp file format
    dot = file[-11:-10]
    if not dot:
        return False
    rid = file[-10:-4]
    return rid.isalnum()


def to_tmp_file(file: str) -> str:
    """
    Convert a filename or directory name to tmp
    filename -> filename.sTD2kF.tmp
    """
    suffix = random_id()
    return f'{file}.{suffix}.tmp'


def to_nontmp_file(file: str) -> str:
    """
    Convert a tmp filename or directory name to original file
    filename.sTD2kF.tmp -> filename
    """
    if is_tmp_file(file):
        return file[:-11]
    else:
        return file

def file_remove(file: str):
    """
    Remove a file non-atomic
    """
    try:
        os.unlink(file)
    except FileNotFoundError:
        # If file not exist, just no need to remove
        pass

def folder_rmtree(folder, may_symlinks=True):
    """
    Recursively remove a folder and its content

    Args:
        folder:
        may_symlinks: Default to True
            False if you already know it's not a symlink

    Returns:
        bool: If success
    """
    try:
        # If it's a symlinks, unlink it
        if may_symlinks and os.path.islink(folder):
            file_remove(folder)
            return True
        # Iter folder
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    folder_rmtree(entry.path, may_symlinks=False)
                else:
                    # File or symlink
                    # Just remove the symlink, not what it points to
                    try:
                        file_remove(entry.path)
                    except PermissionError:
                        # Another process is reading/writing
                        pass

    except FileNotFoundError:
        # directory to clean up does not exist, no need to clean up
        return True
    except NotADirectoryError:
        file_remove(folder)
        return True

    # Remove empty folder
    # May raise OSError if it's still not empty
    try:
        os.rmdir(folder)
        return True
    except FileNotFoundError:
        return True
    except NotADirectoryError:
        file_remove(folder)
        return True
    except OSError:
        return False

def atomic_failure_cleanup(folder: str, recursive: bool = False):
    """
    Cleanup remaining temp file under given path.
    In most cases there should be no remaining temp files unless write process get interrupted.

    This method should only be called at startup
    to avoid deleting temp files that another process is writing.
    """
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if is_tmp_file(entry.name):
                    try:
                        # Delete temp file or directory
                        if entry.is_dir(follow_symlinks=False):
                            folder_rmtree(entry.path, may_symlinks=False)
                        else:
                            file_remove(entry.path)
                    except PermissionError:
                        # Another process is reading/writing
                        pass
                    except:
                        pass
                else:
                    if recursive:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                # Normal directory
                                atomic_failure_cleanup(entry.path, recursive=True)
                        except:
                            pass

    except FileNotFoundError:
        # directory to clean up does not exist, no need to clean up
        pass
    except NotADirectoryError:
        file_remove(folder)
    except:
        # Ignore all failures, it doesn't matter if tmp files still exist
        pass
