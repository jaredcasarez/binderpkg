"""Card-placement history and the undo/redo stack for binder layouts."""


from .models import BinderData


class BinderHistory:
    """History of placed cards for quick re-placement."""

    def __init__(self, max_size: int = 50):
        self.max_size = max_size
        self.history: list[str] = []

    def add(self, url: str) -> None:
        """Add a card URL to history."""
        if url in self.history:
            self.history.remove(url)
        self.history.insert(0, url)
        if len(self.history) > self.max_size:
            self.history.pop()

    def get_recent(self) -> list[str]:
        """Get recent card URLs."""
        return self.history[:]

class BinderUndoRedo:
    """Undo/redo stack for binder layout changes."""

    def __init__(self):
        self.undo_stack: list[BinderData] = []
        self.redo_stack: list[BinderData] = []

    def push_state_change(self, binder_data: BinderData) -> None:
        """Push current state to undo stack."""
        # print(f"Current undo stack size: {len(self.undo_stack)}")
        # print(f"Current redo stack size: {len(self.redo_stack)}")
        # print(f"Current binder data: {binder_data.to_json()}")
        # print(f"Last undo stack state: {self.undo_stack[-1].to_json() if self.undo_stack else 'None'}")
        diffs=None
        if self.undo_stack:
            diffs = self.diff(self.undo_stack[-1], binder_data)
            if diffs is None or (not diffs['binder'] and not diffs['pages'] and not diffs['slots']):
                # print("Undo stack unchanged; not pushing duplicate state.")
                return  # Avoid duplicate states
        # print(f"Pushing to undo stack: {binder_data.to_json()}")
        self.undo_stack.append(BinderData.from_json(binder_data.to_json()))
        # if len(self.undo_stack) > 1:
        #     diffs = BinderUndoRedo.diff(self.undo_stack[-1], self.undo_stack[-2])
        #     if diffs:
        #         print(f"Diff detected: {diffs}")
        #     else:
        #         self.undo_stack.pop()  # Remove the last state if no diffs
        # else:
        #     diffs = None
        self.redo_stack.clear()
        return diffs
        

    def undo(self) -> BinderData | None:
        """Undo last change and return previous state."""
        if not self.undo_stack:
            return None
        last_state = BinderData.from_json(self.undo_stack.pop().to_json())
        if self.undo_stack:
            self.redo_stack.append(last_state)
            return BinderData.from_json(self.undo_stack[-1].to_json())
        else:
            self.redo_stack.append(last_state)
            return None

    def redo(self) -> BinderData | None:
        """Redo last undone change and return next state."""
        if not self.redo_stack:
            return None
        next_state = self.redo_stack.pop()
        self.undo_stack.append(next_state)
        return BinderData.from_json(next_state.to_json())
    @classmethod
    def diff(cls, _a, _b):
        diffs = {'binder':[], 'pages':[], 'slots':[]}
        a = _a.to_json()
        b = _b.to_json()
        if a.get("cols", None) != b.get("cols", None) or a.get("rows", None) != b.get("rows", None):
            diffs['binder'].append('grid')

        for color in ["border_col", "sel_color", "slot_color", "sleeved", "sleeve_color"]:
            if a.get(color, None) != b.get(color, None):
                diffs['binder'].append("color")
                break
        
        if a.get("page_sel", None) != b.get("page_sel", None):
            if a.get("page_sel", None) is not None:
                diffs["pages"].append(a.get("page_sel", None))
            if b.get("page_sel", None) is not None:
                diffs["pages"].append(b.get("page_sel", None))
        for page_idx, (page, diff_page) in enumerate(zip(a.get("pages", []), b.get("pages", []))):
            if page != diff_page:
                diffs['pages'].append(page_idx)
                for slot_idx, (slot, diff_slot) in enumerate(zip(page.get("slots", []), diff_page.get("slots", []))):
                    if slot != diff_slot:
                        diffs['slots'].append((page_idx, slot_idx))
        # for attr in a.__dict__:
        #     if getattr(a, attr, None) != getattr(b, attr,None):
        #         diffs['settings'].append(attr)
        return diffs if diffs['binder'] or diffs['pages'] or diffs['slots'] else None
