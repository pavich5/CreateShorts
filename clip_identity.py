"""Look up original clip identities in filtered or out-of-order result lists."""


def find_clip_by_index(clips, clip_index):
    """Return a clip by its metadata slot, retaining legacy positional lists."""
    if type(clip_index) is not int or clip_index < 0:
        return None
    for position, clip in enumerate(clips or []):
        if not isinstance(clip, dict):
            continue
        index = clip.get('index')
        if index is None:
            index = position
        if type(index) is int and index == clip_index:
            return clip
    return None
