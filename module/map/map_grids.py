import typing as t


class SelectedGrids:
    def __init__(self, grids):
        self.grids = grids
        self.indexes: t.Dict[tuple, SelectedGrids] = {}

    def __iter__(self):
        return iter(self.grids)

    def __getitem__(self, item):
        if isinstance(item, int):
            return self.grids[item]
        else:
            return SelectedGrids(self.grids[item])

    def __contains__(self, item):
        return item in self.grids

    def __str__(self):
        # return str([str(grid) for grid in self])
        return '[' + ', '.join([str(grid) for grid in self]) + ']'

    def __len__(self):
        return len(self.grids)

    def select(self, **kwargs):
        """
        Args:
            **kwargs: Attributes of Grid.

        Returns:
            SelectedGrids:
        """

        def matched(obj):
            flag = True
            for k, v in kwargs.items():
                obj_v = obj.__getattribute__(k)
                if type(obj_v) != type(v) or obj_v != v:
                    flag = False
            return flag

        return SelectedGrids([grid for grid in self.grids if matched(grid)])

    def _select(self, key, values):
        """
        Args:
            key: Attributes of Grid.
        Returns:
            SelectedGrids:
        """

        def matched(obj):
            flag = False
            for v in values:
                obj_v = obj.__getattribute__(key)
                if type(obj_v) == type(v) and obj_v == v:
                    flag = True
            return flag

        return SelectedGrids([grid for grid in self.grids if matched(grid)])

    def first_or_none(self):
        """
        Returns:

        """
        if self:
            return self.grids[0]
        else:
            return None

    def delete(self, grids):
        """
        Args:
            grids(SelectedGrids):

        Returns:
            SelectedGrids:
        """
        g = [grid for grid in self.grids if grid not in grids]
        return SelectedGrids(g)

    @property
    def count(self):
        """
        Returns:
            int:
        """
        return len(self.grids)

    def sort(self, key, index=0):
        self.grids.sort(key=lambda x: x.__getattribute__(key)[index])
