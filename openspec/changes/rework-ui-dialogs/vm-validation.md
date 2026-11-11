# VM検証記録（2026-11-11）

Cua VM（GTK 4.14 prefix・GSK_RENDERER=cairo・jaカタログ再生成済み）で実画面確認した。個人情報は含まない。

## 1.1 対話の閉じ手段

- Settings・Sourcesの各浮き板の右下に「Close」ボタンが現れ、押すと対話が閉じることを確認。
- Escapeキーでも閉じることを確認。a11yツリーには`sheet.close`の操作が残る。
- 試験: `test_dialogs_have_a_visible_close_button`。

## 1.2 ソース一覧の収容

- 一覧が最大高さ320pxの`Gtk.ScrolledWindow`に入り、Refresh・Cancelはスクロール外に置かれたまま押せることを確認。
- 試験: `test_source_list_is_scrolled_and_controls_stay_reachable`（50件の疑似ソースで追加確認）。

## 2.1 危険な操作の分離

- 設定対話のスクショで、「Save settings」の下に区切り線、「Danger zone」見出しを挟んで「Delete stored map data」が離れて並ぶことを確認。
- 試験: `test_delete_map_data_is_a_separate_danger_section`。

## 2.2 見出しと操作要素の区別

- 主画面のセクション見出し（Contacts〜PDF）が平たく左寄せの折りたたみ操作になり、Tasks領域のToday/Upcoming/No due dateのタブ（標準の外観）と視覚で区別できることを確認。a11y上はタブが`tab`、見出しが`toggle button`として読める。
- 試験: `test_section_headings_do_not_wear_button_chrome`。

## 6.1 全回帰

- `runtests.sh`（unittest discover）で440試験がすべて通過（2026-11-11時点）。
