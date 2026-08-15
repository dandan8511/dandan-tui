import unittest

from kernel_manager import (
    KernelIdentity,
    mainline_package_plan,
    parse_grub_menu_entries,
    recommended_apt_plan,
    supported_distribution,
)


class KernelIdentityTests(unittest.TestCase):
    def test_supports_requested_debian_and_ubuntu_versions_on_both_architectures(self):
        supported = (
            ("debian", "11", "bullseye", "amd64"),
            ("debian", "12", "bookworm", "arm64"),
            ("debian", "13", "trixie", "amd64"),
            ("ubuntu", "22.04", "jammy", "arm64"),
            ("ubuntu", "24.04", "noble", "amd64"),
            ("ubuntu", "26.04", "resolute", "arm64"),
        )
        for distro_id, version_id, codename, architecture in supported:
            with self.subTest(distro_id=distro_id, version_id=version_id, architecture=architecture):
                self.assertTrue(
                    supported_distribution(KernelIdentity(distro_id, version_id, codename, architecture))
                )

    def test_rejects_unsupported_release_and_architecture(self):
        self.assertFalse(supported_distribution(KernelIdentity("debian", "10", "buster", "amd64")))
        self.assertFalse(supported_distribution(KernelIdentity("ubuntu", "24.04", "noble", "armhf")))


class AptPlanTests(unittest.TestCase):
    def test_debian_stock_plan_requires_both_image_and_headers_candidates(self):
        identity = KernelIdentity("debian", "12", "bookworm", "amd64")
        self.assertIsNone(recommended_apt_plan(identity, {"linux-image-amd64": "6.1.0-35"}))

        plan = recommended_apt_plan(
            identity,
            {"linux-image-amd64": "6.1.0-35", "linux-headers-amd64": "6.1.0-35"},
        )

        self.assertEqual(plan.label, "Debian 12 官方稳定内核")
        self.assertEqual(plan.packages, ("linux-image-amd64", "linux-headers-amd64"))
        self.assertEqual(plan.source, "configured-apt")

    def test_ubuntu_hwe_plan_is_only_offered_when_apt_exposes_it(self):
        identity = KernelIdentity("ubuntu", "22.04", "jammy", "amd64")
        self.assertIsNone(recommended_apt_plan(identity, {}))

        plan = recommended_apt_plan(
            identity,
            {
                "linux-generic-hwe-22.04": "6.8.0.85.86",
                "linux-headers-generic-hwe-22.04": "6.8.0.85.86",
            },
            preferred_track="hwe",
        )

        self.assertEqual(plan.label, "Ubuntu 22.04 HWE 内核")
        self.assertEqual(
            plan.packages,
            ("linux-generic-hwe-22.04", "linux-headers-generic-hwe-22.04"),
        )


class MainlinePlanTests(unittest.TestCase):
    COMPLETE_AMD64_PAGE = """
        Test amd64 succeeded
        Test arm64 missing
        CHECKSUMS
        CHECKSUMS.gpg
        linux-headers-7.1.8-070108_7.1.8-070108.202608141745_all.deb
        linux-headers-7.1.8-070108-generic_7.1.8-070108.202608141745_amd64.deb
        linux-image-unsigned-7.1.8-070108-generic_7.1.8-070108.202608141745_amd64.deb
        linux-modules-7.1.8-070108-generic_7.1.8-070108.202608141745_amd64.deb
    """

    def test_only_complete_successful_amd64_build_is_installable(self):
        plan = mainline_package_plan("v7.1.8", self.COMPLETE_AMD64_PAGE, "amd64")

        self.assertIsNotNone(plan)
        self.assertEqual(plan.label, "Ubuntu Mainline v7.1.8")
        self.assertEqual(plan.source, "kernel.ubuntu.com")
        self.assertEqual(len(plan.packages), 4)

    def test_missing_or_arm64_mainline_build_is_not_offered(self):
        missing = self.COMPLETE_AMD64_PAGE.replace("Test amd64 succeeded", "Test amd64 missing")

        self.assertIsNone(mainline_package_plan("v7.1.8", missing, "amd64"))
        self.assertIsNone(mainline_package_plan("v7.1.8", self.COMPLETE_AMD64_PAGE, "arm64"))


class GrubParserTests(unittest.TestCase):
    def test_parser_returns_full_submenu_paths(self):
        config = """
            menuentry 'Ubuntu' {}
            submenu 'Advanced options for Ubuntu' {
                menuentry 'Ubuntu, with Linux 6.8.0-31-generic' {}
            }
        """

        self.assertEqual(
            parse_grub_menu_entries(config),
            (
                "Ubuntu",
                "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-31-generic",
            ),
        )


if __name__ == "__main__":
    unittest.main()
