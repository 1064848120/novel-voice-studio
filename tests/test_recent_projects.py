import tempfile
import unittest
from pathlib import Path

from novel_studio.recent_projects import remove_project


class RecentProjectTests(unittest.TestCase):
    def test_delete_moves_project_to_recoverable_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'Book'
            project.mkdir()
            (project / 'project.json').write_text('{}', encoding='utf-8')
            target = remove_project(project, root)
            self.assertFalse(project.exists())
            self.assertTrue((target / 'project.json').is_file())
            self.assertEqual(target.parent, root / '.deleted')

    def test_delete_rejects_project_outside_projects_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'projects'
            outside = Path(tmp) / 'outside'
            root.mkdir()
            outside.mkdir()
            (outside / 'project.json').write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                remove_project(outside, root)
            self.assertTrue((outside / 'project.json').exists())


if __name__ == '__main__':
    unittest.main()
