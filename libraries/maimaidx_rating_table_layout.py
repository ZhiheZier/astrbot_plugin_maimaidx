from math import ceil


LEVEL_15_COLUMNS = 3
LEVEL_15_START_X = 100
LEVEL_15_START_Y = 500
LEVEL_15_COLUMN_GAP = 425
LEVEL_15_ROW_GAP = 450


def level_15_position(index: int) -> tuple[int, int]:
    """Return the shared card origin for a level 15 chart."""
    row, column = divmod(index, LEVEL_15_COLUMNS)
    return (
        LEVEL_15_START_X + column * LEVEL_15_COLUMN_GAP,
        LEVEL_15_START_Y + row * LEVEL_15_ROW_GAP,
    )


def level_15_rows(chart_count: int) -> int:
    return ceil(chart_count / LEVEL_15_COLUMNS) if chart_count else 0


def level_15_height(chart_count: int) -> int:
    return 650 + level_15_rows(chart_count) * LEVEL_15_ROW_GAP
