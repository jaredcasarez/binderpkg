def get_colors(theme="dark"):
    if theme == "dark":
        COLOR_CANVAS_BG = "#26282c"
        COLOR_PAGE_BG = "#3a3d42"
        COLOR_SLOT_EMPTY = "#26282c"
        COLOR_BORDER = "#65625B"
        COLOR_BORDER_SEL = "#4488ff"
        DEFAULT_SLEEVE = "#000000"
        return {
            "canvas_bg": COLOR_CANVAS_BG,
            "page_bg": COLOR_PAGE_BG,
            "slot_empty": COLOR_SLOT_EMPTY,
            "border": COLOR_BORDER,
            "border_sel": COLOR_BORDER_SEL,
            "default_sleeve": DEFAULT_SLEEVE,
        }
    elif theme == "light":
        COLOR_CANVAS_BG = "#e6e6e6"
        COLOR_PAGE_BG = "#f0f0f0"
        COLOR_SLOT_EMPTY = "#e6e6e6"
        COLOR_BORDER = "#c0b8a8"
        COLOR_BORDER_SEL = "#4488ff"
        DEFAULT_SLEEVE = "#010101"
        return {
        "canvas_bg": COLOR_CANVAS_BG,
        "page_bg": COLOR_PAGE_BG,
        "slot_empty": COLOR_SLOT_EMPTY,
        "border": COLOR_BORDER,
        "border_sel": COLOR_BORDER_SEL,
        "default_sleeve": DEFAULT_SLEEVE,
    }

