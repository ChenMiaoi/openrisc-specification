#!/usr/bin/env python3

# Author: Makefile for RISC-V ISA Manuals
# Author: build.py for Chen Miao(chenmiao.ku@gmail.com)
#
# This work is licensed under the Creative Commons Attribution-ShareAlike 4.0
# International License. To view a copy of this license, visit
# http://creativecommons.org/licenses/by-sa/4.0/ or send a letter to
# Creative Commons, PO Box 1866, Mountain View, CA 94042, USA.

"""
OpenRISC Manual Build Script

This script automates the process of building OpenRISC documentation in multiple formats (PDF, HTML, EPUB).
It replicates the functionality of the original Makefile but provides more flexibility and better error handling.

Usage:
    python build.py [options] [targets]

Options:
    --release-type TYPE    Set build type (draft, intermediate, official)
    --clean                Clean build artifacts
    --verbose              Show detailed build output
    --help                 Show this help message

Targets:
    all        Build all formats (default)
    pdf        Build PDF only
    html       Build HTML only
    epub       Build EPUB only
    tags       Build norm tags only
"""

import os
import pathlib
import shutil
import subprocess
import sys
import argparse
import inspect
import json
import tempfile
import re
from datetime import datetime
from os import mkdir, makedirs, environ
from typing import List, Dict, Optional


# Color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


# Constants
ROOT_PATH = os.path.dirname(os.path.abspath(__file__))

DEPENDENCIES = {
    "packages": {
        "apt": ["bison", "build-essential", "cmake", "curl", "flex", "fonts-lyx", "graphviz", "bundler",
                "default-jre", "libcairo2-dev", "libffi-dev", "libgdk-pixbuf2.0-dev", "libpango1.0-dev",
                "libxml2-dev", "make", "pkg-config", "ruby", "ruby-dev", "libwebp-dev", "libzstd-dev"],
    },
    "ruby": {
        "config": f"{ROOT_PATH}/Gemfile",
        "install_path": f"{ROOT_PATH}/vendor/bundle",
    },
    "node": {
        "url": "curl -o- https://fnm.vercel.app/install | bash",
        "version": "20",
        "required": "wavedrom-cli",
        "install_path": f"{ROOT_PATH}/node_modules/.bin",
    },
}

CONFIGS = {
    "source": "openrisc-manual.adoc",
    "docs": {
        "pdf": "openrisc-manual.pdf",
        "html": "openrisc-manual.html",
        "epub": "openrisc-manual.epub",
        "json": "openrisc-manual-norm-tags.json",
    },
    "version": "1.4.0",
    "env": "C.utf8",
    "default_type": "draft",
    "build_dir": f"{ROOT_PATH}/build",
    "build_target": "all",
    "build_cmd": {
        "pdf": ["bundle", "exec", "asciidoctor-pdf"],
        "html": ["bundle", "exec", "asciidoctor"],
        "epub": ["bundle", "exec", "asciidoctor-epub3"],
        "json": ["bundle", "exec", "asciidoctor", "--backend", "tags",
                 "--require=./docs-resources/converters/tags.rb"],
    },
    "type_config": {
        "draft": {
            "watermark_opt": "-a draft-watermark",
            "description": "'DRAFT---NOT AN OFFICIAL RELEASE'",
        },
        "intermediate": {
            "watermark_opt": "",
            "description": "'Intermediate Release'",
        },
        "official": {
            "watermark_opt": "",
            "description": "'Official Release'",
        },
    },
    "required_source": [f"{ROOT_PATH}/docs-resources/global-config.adoc"]
}

OPTIONS = [
    "--trace",
    "-a", "compress",
    "-a mathematical-format=svg",
    "-a pdf-fontsdir=docs-resources/fonts",
    "-a pdf-theme=docs-resources/themes/openrisc-pdf.yml",
    "-a docinfo=shared",
    "-D build",
    f"-a bibtex-file={ROOT_PATH}/assets/resource/openrisc.bib",
    "--failure-level=WARN",
]

REQUIRES = [
    "--require=asciidoctor-bibtex",
    "--require=asciidoctor-diagram",
    "--require=asciidoctor-lists",
    "--require=asciidoctor-mathematical",
    "--require=asciidoctor-sail",
]


class Logger:
    """Enhanced logger with colors, function names and line numbers"""

    def __init__(self, verbose=False):
        self.verbose = verbose

    @staticmethod
    def _get_caller_info():
        """Get the calling function name and line number"""
        frame = inspect.currentframe().f_back.f_back
        filename = os.path.basename(frame.f_code.co_filename)
        return f"{filename}:{frame.f_lineno}"

    @staticmethod
    def _colorize(message: str, color: str) -> str:
        """Add color to the message if output is a terminal"""
        if sys.stdout.isatty():
            return f"{color}{message}{Colors.ENDC}"
        return message

    def log(self, message: str, level: str = "INFO", color: str = ""):
        """Base logging function"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        caller_info = self._get_caller_info()
        colored_level = self._colorize(level, color)
        print(f"[{timestamp}] [{colored_level}] [{caller_info}] {message}")

    def debug(self, message: str):
        """Debug level logging"""
        if self.verbose:
            self.log(message, "DEBUG", Colors.OKBLUE)

    def info(self, message: str):
        """Info level logging"""
        self.log(message, "INFO", Colors.OKGREEN)

    def warning(self, message: str):
        """Warning level logging"""
        self.log(message, "WARNING", Colors.WARNING)

    def error(self, message: str):
        """Error level logging"""
        self.log(message, "ERROR", Colors.FAIL)
        sys.exit(1)

    def success(self, message: str):
        """Success message logging"""
        self.log(message, "SUCCESS", Colors.OKGREEN + Colors.BOLD)


class DocumentBuilder:
    def __init__(self, config, verbose=False):
        self.logger = Logger(verbose)
        self.env = environ.copy()
        self.env["LANG"] = config["env"]
        self.verbose = verbose
        self.version = config["version"]

        self.build_cmd = config["build_cmd"]
        self.build_time = datetime.now().strftime('%Y%m%d')
        self.build_dir = config["build_dir"]
        self.build_type = config["default_type"]
        self.build_target = config["build_target"]
        self.ruby_bin = ""
        self.ruby_lib = ""
        self.required_source = config["required_source"]
        self.work_dir = {}

        if self.build_target == "all":
            self.docs = [
                config["docs"]["json"],
                config["docs"]["pdf"],
                config["docs"]["html"],
                config["docs"]["epub"]]
        else:
            self.docs = [config["docs"][self.build_target]]

        self.options = OPTIONS
        self.options.append(config["type_config"][self.build_type]["watermark_opt"])
        if self.build_type == "draft":
            self.options.append(f"-a revnumber={self.build_time}")
        else:
            self.options.append(f"-a revnumber={self.version}")
        self.options.append(f"-a revremark={config['type_config'][self.build_type]['description']}")

    @staticmethod
    def _check_distribution() -> str:
        """Check the Linux distribution and return package manager"""
        try:
            with open('/etc/os-release', 'r') as f:
                os_release = f.read()
            if 'ubuntu' in os_release.lower() or 'debian' in os_release.lower():
                return 'apt'
            return 'unknown'
        except FileNotFoundError:
            return 'unknown'

    @staticmethod
    def _check_package_installed(package: str) -> bool:
        """Check if a package is installed using dpkg"""
        try:
            result = subprocess.run(
                ['dpkg', '-s', package],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )
            return 'Status: install ok installed' in result.stdout.decode()
        except subprocess.CalledProcessError:
            return False

    def _install_packages(self, packages: List[str]) -> bool:
        """Install packages using apt"""
        self.logger.info(f"Installing packages: {', '.join(packages)}")
        try:
            subprocess.run(
                ['sudo', 'apt-get', 'install', '-y'] + packages,
                check=True,
                stdout=subprocess.PIPE if not self.verbose else None,
                stderr=subprocess.PIPE if not self.verbose else None
            )
            self.logger.success(f"Successfully installed packages: {', '.join(packages)}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to install packages: {e.stderr.decode().strip()}")
            return False

    def _setup_node_environment(self, node_config: Dict) -> bool:
        """Set up Node environment using fnm"""
        try:
            # Check if node is installed
            try:
                subprocess.run(["node", "--version"], check=True, capture_output=True)
                node_installed = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                node_installed = False

            # If not installed, install using node_config["url"]
            if not node_installed:
                subprocess.run(node_config["url"], check=True)

                if "version" in node_config:
                    subprocess.run(["fnm", "install", node_config["version"]], check=True,
                                   stdout=subprocess.PIPE if not self.verbose else None,
                                   stderr=subprocess.PIPE if not self.verbose else None)
                    subprocess.run(["fnm", "use", node_config["version"]], check=True,
                                   stdout=subprocess.PIPE if not self.verbose else None,
                                   stderr=subprocess.PIPE if not self.verbose else None)

                # Verify installation
                subprocess.run(["node", "--version"], check=True)
            self.logger.success(f"Successfully installed node, version: {node_config['version']}")

            # Check for package.json and required dependencies
            if os.path.exists(f"{ROOT_PATH}/package.json"):
                subprocess.run(["npm", "install"], check=True,
                               stdout=subprocess.PIPE if not self.verbose else None,
                               stderr=subprocess.PIPE if not self.verbose else None)
            else:
                self.logger.error(f"Could not find package.json in {ROOT_PATH}/package.json")
                return False

            # Add install path to environment PATH
            if "install_path" in node_config:
                self.env["PATH"] = f"{node_config['install_path']}:{self.env['PATH']}"

            self.logger.success("Node environment setup completed")
            return True

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Node environment setup failed: {e.stderr.decode().strip()}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during Node environment setup: {str(e)}")
            return False

    def _setup_ruby_environment(self, ruby_config: Dict, ruby_require: List) -> bool:
        """Set up Ruby environment using Bundler"""
        try:
            # Check if bundle is installed
            self.logger.debug("Checking bundle version...")
            subprocess.run(['bundle', '--version'],
                           check=True,
                           stdout=subprocess.PIPE if not self.verbose else None,
                           stderr=subprocess.PIPE if not self.verbose else None)

            self.logger.info("Setting up Ruby environment...")

            # bundle config set --local path ruby_config["install_path"]
            self.logger.debug("Configuring bundle path...")
            subprocess.run([
                'bundle', 'config', 'set', '--local', 'path',
                ruby_config["install_path"]
            ], check=True,
                stdout=subprocess.PIPE if not self.verbose else None,
                stderr=subprocess.PIPE if not self.verbose else None)

            # bundle install
            self.logger.debug("Running bundle install...")
            subprocess.run(['bundle', 'install'],
                           check=True,
                           stdout=subprocess.PIPE if not self.verbose else None,
                           stderr=subprocess.PIPE if not self.verbose else None)

            ruby_version = "3.3.0"  # Simplified for example

            self.ruby_bin = f"{ruby_config['install_path']}/ruby/{ruby_version}/bin"
            self.ruby_lib = f"{ruby_config['install_path']}/ruby/{ruby_version}/gems"
            self.logger.debug(f"Ruby Binary in {self.ruby_bin}")
            self.logger.debug(f"Ruby Library in {self.ruby_lib}")

            new_path = f"{self.ruby_bin}:{environ['PATH']}"
            self.env["PATH"] = new_path
            self.env["RUBYLIB"] = self.ruby_lib
            self.ruby_require = [s.replace("REPLACE", self.ruby_lib) for s in ruby_require]

            self.logger.success("Ruby environment setup completed")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Ruby environment setup failed: {e.stderr.decode().strip()}")
            return False
        except FileNotFoundError:
            self.logger.error("Bundle command not found. Please install Bundler first.")
            return False

    def _workdir_setup(self):
        """Set up working directories for each build target"""
        for doc in self.docs:
            work_path = os.path.join(self.build_dir, f"{doc}.workdir")
            if not os.path.exists(work_path):
                self.logger.debug(f"Creating work directory: {work_path}")
                makedirs(work_path, exist_ok=True)
                os.symlink(f"{ROOT_PATH}/src", f"{work_path}/src", target_is_directory=True)
                os.symlink(f"{ROOT_PATH}/docs-resources", f"{work_path}/docs-resources", target_is_directory=True)
                os.symlink(f"{ROOT_PATH}/assets", f"{work_path}/assets", target_is_directory=True)
                self.logger.debug(f"Created work directory: {work_path}")

            build_targs = doc.split(".")[1]
            self.work_dir[build_targs] = work_path

    def update_wave(self) -> bool:
        """Update wave by extracting content between .... markers"""
        self.logger.debug("Updating wave...")

        if not self._setup_node_environment(DEPENDENCIES["node"]):
            return False

        wave_path = f"{ROOT_PATH}/assets/images/wavedrom/edn"
        edn_files = list(pathlib.Path(wave_path).glob("*.edn"))
        svg_path = f"{ROOT_PATH}/assets/images/wavedrom/svg"

        self.logger.debug(f"Extracting content from {len(edn_files)} files")

        for edn_file in edn_files:
            svg_file = f"{svg_path}/{edn_file.with_suffix('.svg').name}"

            try:
                # Read the EDN file content
                with open(edn_file, 'r') as f:
                    content = f.read()

                # Extract content between .... markers
                pattern = r'^\.\.\.\.\n(.*?)\n\.\.\.\.$'
                match = re.search(pattern, content, re.DOTALL | re.MULTILINE)

                if not match:
                    self.logger.error(f"No content found between .... markers in {edn_file}")
                    return False

                wave_content = match.group(1)

                # Create a temporary file with just the wave content
                with tempfile.NamedTemporaryFile(mode='w+', suffix='.edn', delete=False) as tmp:
                    tmp.write(wave_content)
                    tmp_path = tmp.file.name

                # Run wavedrom-cli on the temporary file
                subprocess.run([
                    'wavedrom-cli', '-i', tmp_path, '-s', str(svg_file)],
                    check=True,
                    env=self.env,
                    stdout=subprocess.PIPE if not self.verbose else None,
                    stderr=subprocess.PIPE if not self.verbose else None)

                # Clean up the temporary file
                os.unlink(tmp_path)

            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to generate {svg_file}: {e}")
                return False
            except Exception as e:
                self.logger.error(f"Error processing {edn_file}: {e}")
                return False

        self.logger.success(f"Successfully updated wavedrom: {svg_path}")
        return True

    def build_for_all(self, build_target: str):
        """Build documentation for a specific target"""
        cmd = []
        cmd.extend(self.build_cmd[build_target])

        if build_target == "json":
            cmd.append("-a tags-match-prefix='norm:' -a tags-output-suffix='-norm-tags.json'")

        cmd.extend(self.options)
        cmd.extend(self.ruby_require)
        cmd.append(f"src/{CONFIGS['source']}")

        cmd_str = " ".join(cmd)
        os.chdir(self.work_dir[build_target])

        self.logger.info(f"Building {build_target.upper()} output...")
        self.logger.debug(f"Running command: {cmd_str}")

        try:
            result = subprocess.run(
                cmd_str,
                env=self.env,
                check=True,
                shell=True,
                stdout=subprocess.PIPE if not self.verbose else None,
                stderr=subprocess.PIPE if not self.verbose else None,
                text=True,
            )
            self.logger.success(f"Successfully built {build_target.upper()} output")
        except subprocess.CalledProcessError as e:
            self.logger.error(
                f"Failed to build {build_target}: {e.stderr.decode().strip() if e.stderr else 'Unknown error'}")

    def prepare_env(self) -> bool:
        """Prepare the build environment by installing dependencies"""
        self.logger.info("Starting environment preparation...")

        # Check distribution and install system packages
        distro = self._check_distribution()
        if distro == 'apt':
            packages_to_install = []
            for package in DEPENDENCIES['packages']['apt']:
                if not self._check_package_installed(package):
                    packages_to_install.append(package)

            if packages_to_install:
                if not self._install_packages(packages_to_install):
                    return False
            else:
                self.logger.info("All required packages are already installed")
        else:
            self.logger.error(f"Unsupported distribution: {distro}")
            return False

        # Setup Ruby environment
        if not self._setup_ruby_environment(DEPENDENCIES['ruby'], REQUIRES):
            return False

        # Setup Node environment
        if not self._setup_node_environment(DEPENDENCIES["node"]):
            return False

        # Verify required source files exist
        for source in self.required_source:
            if not os.path.exists(source):
                self.logger.warning(f"Required source file not found: {source}")
                self.logger.warning(
                    "You must clone with --recurse-submodules to automatically populate the submodule 'docs-resources'.")
                self.logger.info("Checking out submodules for you via 'git submodule update --init --recursive'...")
                try:
                    subprocess.run(["git", "submodule", "update", "--init", "--recursive"],
                                   check=True,
                                   stdout=subprocess.PIPE if not self.verbose else None,
                                   stderr=subprocess.PIPE if not self.verbose else None)
                    self.logger.success("Submodules updated successfully")
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to update submodules: {e.stderr.decode().strip()}")

        self._workdir_setup()

        return True

    def clean_work_dir(self):
        """Clean work directory and remove some files"""
        for doc in self.docs:
            build_targs = doc.split(".")[1]
            src_path = os.path.join(self.work_dir[build_targs], 'build', doc)
            dst_path = os.path.join(self.build_dir, doc)

            shutil.copy2(src_path, dst_path)
            self.logger.info(f"Copying {doc} to {dst_path}")

            shutil.rmtree(self.work_dir[build_targs])


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='OpenRISC Documentation Builder')
    parser.add_argument('--release-type', choices=['draft', 'intermediate', 'official'],
                        default='draft', help='Set build type')
    parser.add_argument('--incremental', action='store_true', help='Enable incremental builds')
    parser.add_argument('--clean', action='store_true', help='Clean build artifacts')
    parser.add_argument('--verbose', action='store_true', help='Show detailed build output')
    parser.add_argument('--version', type=str, default='1.4.0', help='Specify the version number (e.g., 1.0.0)')
    parser.add_argument("--node-version", type=str, default='20', help='Specify the version number (e.g., 20)')
    parser.add_argument("--update-wave", action='store_true', default=False, help='Update Wave')
    parser.add_argument('target', nargs='?', default='all',
                        choices=['all', 'pdf', 'html', 'epub', 'tags'],
                        help='Build target')
    return parser.parse_args()


def main():
    args = parse_args()

    # Update config based on command line arguments
    CONFIGS['default_type'] = args.release_type
    CONFIGS['build_target'] = args.target
    CONFIGS['version'] = args.version
    DEPENDENCIES['node']['version'] = args.node_version

    builder = DocumentBuilder(CONFIGS, verbose=args.verbose)

    if args.clean:
        builder.logger.info("Cleaning build artifacts...")
        if os.path.exists(CONFIGS['build_dir']):
            shutil.rmtree(CONFIGS['build_dir'])
            builder.logger.success("Build directory cleaned")
        else:
            builder.logger.info("Build directory does not exist, nothing to clean")
        return

    if args.update_wave:
        builder.update_wave()
        return

    try:
        if not builder.prepare_env():
            builder.logger.error("Failed to prepare environment")
            return

        if builder.build_target in ["pdf", "json", "epub", "html"]:
            builder.build_for_all(builder.build_target)
        elif builder.build_target == "all":
            builder.build_for_all("pdf")
            builder.build_for_all("epub")
            builder.build_for_all("html")
            builder.build_for_all("json")
        else:
            builder.logger.error(f"Unsupported build target: "
                                 f"{builder.build_target}")

        builder.clean_work_dir()

        builder.logger.success("Build process completed successfully!")

    except Exception as e:
        builder.logger.error(f"Unexpected error during build: {str(e)}")


if __name__ == "__main__":
    main()