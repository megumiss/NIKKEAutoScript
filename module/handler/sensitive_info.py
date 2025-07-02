import re


def handle_sensitive_text(text):
    """
    Args:
        text (str):

    Returns:
        str:
    """
    text = re.sub('File "(.*?)NKAS', 'File "C:\\\\fakepath\\\\NKAS', text, flags=re.IGNORECASE)
    text = re.sub(
        '\[AdbBinary\] (.*?)NKAS',
        '[AdbBinary] C:\\\\fakepath\\\\NKAS',
        text,
        flags=re.IGNORECASE,
    )
    return text


def handle_sensitive_logs(logs):
    return [handle_sensitive_text(line) for line in logs]
