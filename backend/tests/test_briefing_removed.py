import unittest

from api import app


class BriefingRemovalTests(unittest.TestCase):
    def test_briefing_route_is_not_registered(self):
        route_paths = {
            route.path for route in app.routes if hasattr(route, "path")
        }

        self.assertNotIn("/api/briefing", route_paths)


if __name__ == "__main__":
    unittest.main()
