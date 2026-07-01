VIDEO_EXTENSIONS = {'mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv'}
AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'aac', 'flac'}
PDF_EXTENSIONS = {'pdf'}
POWERPOINT_EXTENSIONS = {'ppt', 'pptx'}
DOC_EXTENSIONS = {'doc', 'docx'}
IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'}
TEXT_EXTENSIONS = {'txt', 'json', 'csv', 'xml', 'log', 'ini', 'cfg', 'yaml', 'yml', 'md', 'html', 'js', 'css'}


def classify_file_type(ext):
    ext = (ext or '').lower()
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    if ext in AUDIO_EXTENSIONS:
        return 'audio'
    if ext in PDF_EXTENSIONS:
        return 'pdf'
    if ext in POWERPOINT_EXTENSIONS:
        return 'powerpoint'
    if ext in DOC_EXTENSIONS:
        return 'docx'
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    if ext in TEXT_EXTENSIONS:
        return 'text'
    return 'unknown'
