from distutils import log
import contextlib
import importlib
import os
import shutil
import subprocess
import sys

from setuptools.command.build_py import build_py
import pkg_resources
import setuptools


class Command(setuptools.Command):

    def build_in_place(self):
        self.run_command('build_py')
        self.reinitialize_command('build_ext', inplace=1)
        self.run_command('build_ext')

    @contextlib.contextmanager
    def project_on_sys_path(self):
        self.build_in_place()
        ei_cmd = self.get_finalized_command("egg_info")
        old_path = sys.path[:]
        old_modules = sys.modules.copy()
        try:
            sys.path.insert(0, pkg_resources.normalize_path(ei_cmd.egg_base))
            pkg_resources.working_set.__init__()
            pkg_resources.add_activation_listener(lambda dist: dist.activate())
            pkg_resources.require('%s==%s' % (ei_cmd.egg_name, ei_cmd.egg_version))
            yield
        finally:
            sys.path[:] = old_path
            sys.modules.clear()
            sys.modules.update(old_modules)
            pkg_resources.working_set.__init__()

# `test` command. {{{

class Test(Command):

    description = 'run unit tests after in-place build'
    command_consumes_arguments = True
    user_options = []
    test_dir = 'test'

    def initialize_options(self):
        self.args = []

    def finalize_options(self):
        pass

    def run(self):
        with self.project_on_sys_path():
            self.run_tests()

    def run_tests(self):
        # Remove __pycache__ directory so pytest does not freak out
        # when switching between the Linux/Windows versions.
        pycache = os.path.join(self.test_dir, '__pycache__')
        if os.path.exists(pycache):
            shutil.rmtree(pycache)
        custom_testsuite = None
        args = []
        for a in self.args:
            if '-' == a[0]:
                args.append(a)
            elif os.path.exists(a):
                custom_testsuite = a
                args.append(a)
            else:
                args.extend(('-k', a))
        if custom_testsuite is None:
            args.insert(0, self.test_dir)
        sys.argv[1:] = args
        main = pkg_resources.load_entry_point('pytest',
                                              'console_scripts',
                                              'py.test')
        sys.exit(main())

# }}}

# UI generation. {{{

class BuildUi(Command):

    description = 'build UI files'
    user_options = [
        ('force', 'f',
         'force re-generation of all UI files'),
    ]

    hooks = '''
    plover_build_utils.pyqt:fix_icons
    plover_build_utils.pyqt:gettext
    '''.split()

    def initialize_options(self):
        self.force = False

    def finalize_options(self):
        pass

    def _build_ui(self, src):
        dst = os.path.splitext(src)[0] + '_ui.py'
        if not self.force and os.path.exists(dst) and \
           os.path.getmtime(dst) >= os.path.getmtime(src):
            return
        cmd = (
            sys.executable, '-m', 'PyQt5.uic.pyuic',
            '--from-import', src,
        )
        if self.verbose:
            log.info('generating %s', dst)
        contents = subprocess.check_output(cmd).decode('utf-8')
        for hook in self.hooks:
            mod_name, attr_name = hook.split(':')
            mod = importlib.import_module(mod_name)
            hook_fn = getattr(mod, attr_name)
            contents = hook_fn(contents)
        with open(dst, 'w') as fp:
            fp.write(contents)

    def _build_resources(self, src):
        dst = os.path.join(
            os.path.dirname(os.path.dirname(src)),
            os.path.splitext(os.path.basename(src))[0]
        ) + '_rc.py'
        cmd = (
            sys.executable, '-m', 'PyQt5.pyrcc_main',
            src, '-o', dst,
        )
        if self.verbose:
            log.info('generating %s', dst)
        subprocess.check_call(cmd)

    def run(self):
        self.run_command('egg_info')
        ei_cmd = self.get_finalized_command('egg_info')
        for src in ei_cmd.filelist.files:
            if src.endswith('.qrc'):
                self._build_resources(src)
            if src.endswith('.ui'):
                self._build_ui(src)

# }}}

# Patched `build_py` command. {{{

class BuildPy(build_py):

    build_dependencies = []

    def run(self):
        for command in self.build_dependencies:
            self.run_command(command)
        build_py.run(self)

# }}}


def ensure_setup_requires(setuptools_spec, dependency_links=None, setup_requires=None):
    if 'PYTHONPATH' in os.environ:
        py_path = os.environ['PYTHONPATH'].split(os.pathsep)
    else:
        py_path = []
    # First, ensure the correct version of setuptools is active.
    setuptools_req = next(pkg_resources.parse_requirements('setuptools' + setuptools_spec))
    setuptools_dist = pkg_resources.get_distribution('setuptools')
    if setuptools_dist not in setuptools_req:
        setuptools_dist = setuptools.Distribution().fetch_build_eggs(str(setuptools_req))[0]
        py_path.insert(0, setuptools_dist.location)
        os.environ['PYTHONPATH'] = os.pathsep.join(py_path)
        args = [sys.executable] + sys.argv
        os.execv(args[0], args)
    # Second, install other setup requirements.
    setup_attrs = {}
    if dependency_links is not None:
        setup_attrs['dependency_links'] = dependency_links
    if setup_requires is not None:
        setup_attrs['setup_requires'] = setup_requires
    setup_dist = setuptools.Distribution(setup_attrs)
    setup_dist.parse_config_files(ignore_option_errors=True)
    if not setup_dist.setup_requires:
        return
    eggs_dir = setup_dist.get_egg_cache_dir()
    for dist in setup_dist.fetch_build_eggs(setup_dist.setup_requires):
        if dist.location.startswith(os.path.abspath(eggs_dir) + os.sep):
            py_path.insert(0, dist.location)
    os.environ['PYTHONPATH'] = os.pathsep.join(py_path)
