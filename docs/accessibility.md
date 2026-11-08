# アクセシビリティの実装補強（タスク8.2コード側）

## 具体的な補強内容

自動テストで検証できる範囲として、次のGTKネイティブAPIを使った補強を実装した。これらはテーマのCSS・挙動を変えず、関係付けと読み上げを追加するだけである。

- `Gtk.AccessibleProperty.LABEL` の設定: 言語ドロップダウン（[application_view.py](<../../src/paperphone/application_view.py>)）、連絡先リストボックス（[contact_selection.py](<../../src/paperphone/contact_selection.py>)）、TODOリストボックス（[todo_selection.py](<../../src/paperphone/todo_selection.py>)）、予定・ソースのグループ、地名検索結果一覧。
- `Gtk.Accessible.announce` の使用: 連絡先上限の再選拒否を `HIGH` 優先でも送る。地名検索の進行・結果・取消や、各ソースの状態更新も `MEDIUM` 優先で読み上げる（`check_version(4,14,0)` があるため古いGTK上では黙る）。
- 視認性: TODOの期限切れバッジと連絡先上限メッセージから `dim-label` の透明化を外し、既定のコントラストで必須状態を確認できるようにした。

実機（GTK 4.24のPyGObject）で確認したところ、`update_relation(LABELLED_BY, [widget])` はPyGObjectのGValue変換に失敗し `g_value_get_pointer` の警告を出して関係が付かなかったため、上記グループ名は `update_property(LABEL)` へ移した。この警告が出なくなったことと読み上げ名が付いたことは、次回実GUI確認で確かめる。

## 制限

- これらの読み上げは実機・スクリーンリーダー（Orca等）での確認を行っていない。特にannounceの優先度分け、フォーカス移動、キーボード全操作は手動GUIで検証する前提がある。
- 明・暗テーマの視覚確認も、実際のgnome-terminalや設定を含む環境で行う必要がある。自動テストはCSSクラスの有無だけを確認している。
- これにより8.2を完了とはしない。実操作・表示確認の記録は利用者の環境での試験が必要である。
