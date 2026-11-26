"""Human-readable transfer measurements."""

def format_size(value, decimal=False):
    base = 1000 if decimal else 1024
    units = ('B', 'kB', 'MB', 'GB', 'TB') if decimal else ('B', 'KiB', 'MiB', 'GiB', 'TiB')
    if abs(value) < base:
        return f'{value:,.0f} B'
    for unit in units[1:]:
        value /= base
        if abs(value) < base or unit == units[-1]:
            return f'{value:,.1f} {unit}'


def format_rate(value, decimal=False):
    return format_size(value, decimal) + '/s'
