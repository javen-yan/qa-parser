"""Display references are explicit aliases, never physical resource paths."""
import re

DEFAULT_REFERENCE = '参考aaa{filename})'
# Used only for legacy bare numbered filenames, outside mapped references.
BARE_IMAGE = re.compile(r'(?<![A-Za-z0-9_.-])((?:aaa)?\d{3,}\.(?:png|jpe?g|gif|bmp|tiff?|wmf|emf|svg|webp))(?![\w.-])', re.I)


def reference_map(names, template):
    result = {template.format(filename=name): name for name in names}
    if len(result) != len(names) or any(not ref for ref in result):
        raise ValueError('Ambiguous image reference mapping')
    return result


def referenced_names(text, template):
    # Decode the configured alias, then remove it before legacy matching:
    # 参考aaa001.png) -> 001.png, NOT aaa001.png and NOT a substring match.
    if template.count('{filename}') != 1:
        raise ValueError('Image reference requires one filename placeholder')
    pattern = re.compile(re.escape(template).replace(re.escape('{filename}'),
                        r'(?P<filename>[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)'))
    names = {m['filename'] for m in pattern.finditer(text)}
    remainder = pattern.sub('', text)
    names.update(BARE_IMAGE.findall(remainder))
    return names
