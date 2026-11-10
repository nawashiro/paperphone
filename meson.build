project(
  'paperphone',
  version: '0.1.0',
  meson_version: '>=0.62.0',
)

# Meson自身と同じPythonを使い、再生成時のPATH差で仮想環境を失わない。
python = import('python').find_installation(required: true)
gtk4 = dependency('gtk4', version: '>=4.14')
libadwaita = dependency('libadwaita-1', version: '>=1.0')
subdir('po')
subdir('data')

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

maps_check = run_command(
  python,
  '-c',
  'import osmium; import shapely; import rapidfuzz',
  check: false,
)
if maps_check.returncode() != 0
  error('OSM検索ライブラリがありません。開発環境では `python3 -m pip install -r requirements.txt` を実行してください。')
endif

printing_check = run_command(
  python,
  '-c',
  'import cairo, gi; gi.require_foreign("cairo"); gi.require_versions({"Pango": "1.0", "PangoCairo": "1.0", "PangoFc": "1.0", "Poppler": "0.18", "GdkPixbuf": "2.0"}); from gi.repository import Pango, PangoCairo, PangoFc, Poppler, GdkPixbuf',
  check: false,
)
if printing_check.returncode() != 0
  error('PDF描画・フォント・画像のネイティブ依存がありません。docs/gnome-dependencies.mdを確認してください。')
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
    'src/paperphone/osm_extracts.py',
    'src/paperphone/osm_store.py',
    'src/paperphone/osm_derived.py',
    'src/paperphone/osm_region_view.py',
    'src/paperphone/prefectures.py',
    'src/paperphone/places.py',
    'src/paperphone/place_selection.py',
    'src/paperphone/walking.py',
    'src/paperphone/walking_sheet.py',
    'src/paperphone/map_rendering.py',
    'src/paperphone/booklet_layout.py',
    'src/paperphone/booklet_content.py',
    'src/paperphone/booklet_assets.py',
    'src/paperphone/booklet_asset_view.py',
    'src/paperphone/booklet_maps.py',
    'src/paperphone/printing_fonts.py',
    'src/paperphone/main.py',
    'src/paperphone/application_view.py',
    'src/paperphone/application_services.py',
    'src/paperphone/pdf_output.py',
    'src/paperphone/refresh.py',
    'src/paperphone/refresh_view.py',
    'src/paperphone/resources.py',
    'src/paperphone/selection.py',
    'src/paperphone/temporal.py',
    'src/paperphone/todo.py',
    'src/paperphone/todo_reader.py',
    'src/paperphone/todo_selection.py',
    'src/paperphone/weather.py',
    'src/paperphone/weather_location.py',
    'src/paperphone/weather_location_view.py',
    'src/paperphone/weather_selection.py',
    'src/paperphone/weather_sheet.py',
    'src/paperphone/weather_rendering.py',
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

python_path.set('PAPERPHONE_LOCALEDIR', join_paths(meson.project_source_root(), 'build', 'gnome', 'po'))

# テストだけdconfをメモリ化する。通常起動はGNOMEの実設定に追従する。
python_test_path = environment({
  'PYTHONPATH': source_dir,
  'PAPERPHONE_LOCALEDIR': join_paths(meson.project_build_root(), 'po'),
  'GSETTINGS_BACKEND': 'memory',
})

test(
  'python-tests',
  python,
  args: ['-m', 'unittest', 'discover', '-s', test_dir, '-t', source_root],
  env: python_test_path,
)

run_target(
  'run',
  command: [python, '-m', 'paperphone'],
  env: python_path,
)
