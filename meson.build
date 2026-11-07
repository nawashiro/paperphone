project(
  'paperphone',
  version: '0.1.0',
  meson_version: '>=0.62.0',
)

python = import('python').find_installation('python3', required: true)
gtk4 = dependency('gtk4', version: '>=4.0')
libadwaita = dependency('libadwaita-1', version: '>=1.0')
subdir('po')

# GIの実行時名前空間が利用可能であることも構成時に確認する。
gi_check = run_command(
  python,
  '-c',
  'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw',
  check: false,
)
if gi_check.returncode() != 0
  error('PyGObjectからGTK 4とlibadwaitaを読み込めません。')
endif
calendar_check = run_command(
  python,
  '-c',
  'import icalendar; import dateutil.rrule',
  check: false,
)
if calendar_check.returncode() != 0
  error('予定解析ライブラリがありません。開発環境では `python3 -m pip install -r requirements.txt` を実行してください。')
endif

source_root = meson.project_source_root()
source_dir = join_paths(source_root, 'src')
test_dir = join_paths(source_root, 'tests')
python_path = environment()
python_path.set('PYTHONPATH', source_dir)

python.install_sources(
  files(
    'src/paperphone/__init__.py',
    'src/paperphone/eds.py',
    'src/paperphone/calendar_reader.py',
    'src/paperphone/contact_selection.py',
    'src/paperphone/contacts.py',
    'src/paperphone/events.py',
    'src/paperphone/__main__.py',
    'src/paperphone/i18n.py',
    'src/paperphone/location.py',
    'src/paperphone/main.py',
    'src/paperphone/refresh.py',
    'src/paperphone/refresh_view.py',
    'src/paperphone/resources.py',
    'src/paperphone/selection.py',
    'src/paperphone/temporal.py',
    'src/paperphone/todo.py',
    'src/paperphone/todo_reader.py',
    'src/paperphone/todo_selection.py',
    'src/paperphone/weather.py',
  ),
  subdir: 'paperphone',
)
install_subdir(
  'src/paperphone/resources',
  install_dir: join_paths(python.get_install_dir(), 'paperphone'),
)
install_data(
  'LICENSE',
  install_dir: join_paths(python.get_install_dir(), 'paperphone', 'resources', 'licenses'),
)

python_path.set('PAPERPHONE_LOCALEDIR', join_paths(meson.project_build_root(), 'po'))

test(
  'python-tests',
  python,
  args: ['-m', 'unittest', 'discover', '-s', test_dir],
  env: python_path,
)

run_target(
  'run',
  command: [python, '-m', 'paperphone'],
  env: python_path,
)
