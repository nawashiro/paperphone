# EDS / CalDAV 読み取り専用の実証記録

## 読み取り専用の境界

本アプリはEDSに登録済みのソースを列挙して読む。CalDAV上のTODOを追加・変更・削除したり、完了状態を書き戻したりしない。Thunderbirdのプロファイルや認証情報も読み込まない。

今回の実証では、EDSのSourceManagerからソース一覧を取得し、CalendarFactory / AddressBookFactoryでクライアントを開き、`Open`、`GetObjectList`、`GetContactListUids`、`GetContact`だけを呼び出した。`CreateSources`、`CreateObjects`、`ModifyObjects`、`RemoveObjects`、`CreateContacts`、`ModifyContacts`、`RemoveContacts`などの変更操作は呼び出していない。タイトル、氏名、URL、具体的な日時、ソースUIDは記録・ログへ出していない。

## 実証結果

- EDS SourceManagerから有効なカレンダー、タスクリスト、アドレス帳ソースを列挙できた。
- ECalのカレンダー読取では、範囲を限定した検索からVEVENTを取得し、タイトル・開始・終了フィールドを読み取れた。
- EDSに登録されたリモートWebDAV/CalDAVタスクリストを開き、検索からVTODOを取得した。件名、着手日時、期限、完了状態に使われるフィールドを読み取れた。実データの値は保存していない。
- EBookのローカルアドレス帳からvCardを1件読み、氏名フィールドを確認した。そのレコードに電話番号フィールドはなかったため、電話番号の実データ確認は行っていない。電話番号ありのfixture検証は連絡先移植タスクで行う。
- 現環境では`libecal` / `libebook`のPyGObject名前空間とpkg-config開発メタデータを利用できなかったため、ホストのEDSが公開するバージョン付きD-Busサービス（`Sources5`、`Calendar8`、`AddressBook10`）の読取メソッドをGioから直接呼び出して実証した。これはホストEDSの読取成立を示すが、配布アプリで使うバインディング／最小権限はタスク2.2で別途確認する。
- 読み取ったデータはプロセス内でフィールド有無だけ確認し、ファイルへ保存していない。CalDAVソースへの変更要求は送っていない。
- 今回のGNOME版アダプターによる再読取でも、登録済みリモートタスクリストの状態は`ready`となった。実際のD-Bus呼出しは`OpenTaskList`、`Open`、`GetObjectList`のみで、変更メソッドは0件だった。件名・日時・UID・件数は記録していない。この確認は現在の読取成立を示すもので、外部更新後の差分反映試験ではない。

## アプリの再取得手順と書き込み境界

更新処理は利用者が選んだ有効なEDSソースだけを開き、種類別の読取操作を並行して実行する。カレンダーとローカルタスクリストは`Open`→`GetObjectList`、アドレス帳は`Open`→`GetContactList`を使う。CalDAVタスクリストは次の手順で更新する。

1. ソースの`BackendName`が`caldav`であることを確認し、`Open`後に`GetView(#t)`を呼ぶ。
2. そのクライアントとViewのパスに限定して通知を購読し、`Start`する。
3. `Complete`は初期キャッシュの列挙完了とし、リモート同期完了とはみなさない。ここからEDSへ`Refresh`を要求する。
4. `Refresh`の応答だけでは再取得成功としない。初期列挙後の`ObjectsAdded`・`ObjectsModified`・`ObjectsRemoved`を待ち、`GetObjectList(#t)`でVTODOを再読込する。
5. Refresh受付後10秒以内に変更通知が来ない場合は、キャッシュを読んで`refresh-unconfirmed`とする。変更なしの場合も通知は来ないため、この状態は通信障害や同期失敗の断定ではない。UIでは変更未確認・キャッシュ表示の可能性を明示する。View初期化は30秒を上限とする。
6. 終了・取消時に購読解除とViewの`Dispose`を行う。取消後の遅延応答を無視する。

キャンセルはGioの`Cancellable`を通じて進行中の読取を止める。結果はソース単位に状態とキャッシュを保持し、取得済みの内容はログへ出さない。`Refresh`はキャッシュ同期要求専用の経路に分離し、オブジェクト作成・変更・削除APIは読取許可リストにも登録していない。

CalDAV上の更新反映を実環境で試す場合は、利用者がThunderbird/Evolution等で使い捨てのテストTODOを更新した後、Paper Phone側で同じタスクリストを再取得して表示を確認する。Paper Phone自身からテストTODOの作成・変更・完了・削除は行わない。初回確認は既存データの読取のみだった。後続確認では利用者自身がThunderbirdから完了状態を同期し、修正済みアダプターによる更新前後を比較した（下記）。

## 今回の修正後の実環境確認

- 修正した読取アダプターからホストEDSの登録済みCalDAVタスクリストを開き、CalendarViewの通知を受けた再読込は`ready`となった。
- 利用者が提示した対象TODOを検出し、件名・着手・期限・未完了状態がすべて一致することをメモリ内の比較で確認した。個人データ値・UID・接続先・認証情報は成果物へ保存していない。
- 続いて利用者がThunderbirdで既存TODOを完了にし、同期したとの申告後に更新前後を比較した。最初のEDSキャッシュ読取では対象が未完了だったが、修正済みアダプターのRefresh・CalendarView変更通知待ち・再読込後は`ready`となり、対象は未完了候補に含まれなかった。全VTODOを読む別確認でも同じ対象が完了状態になっていることを確認した。単なる未検出や削除と区別して、実CalDAVの完了変更反映と候補除外を検証できた。タイトル・具体的な日時・UID・接続先は記録していない。アプリからの元データ変更操作は行っていない。
- 回帰テストは、Refresh応答だけで完了扱いにしないこと、初期通知の除外、遅延変更、完了、通知タイムアウト、取消、遅延View破棄、認証失敗時のキャッシュ保持、書込API拒否とUIの未確認表示を検証する。
- gettextカタログを`msgfmt --check`で一時ディレクトリへ構築して`PAPERPHONE_LOCALEDIR`を指定した単体テストは全件成功した。カタログ未構築のまま直接実行した際の翻訳テスト失敗は、コードの不具合ではなく実行条件の不足だった。Mesonコマンドが現環境にないためMeson経由の再ビルドは未実施。

## ThunderbirdとEDSの登録手順

ThunderbirdとEDSは別々のカレンダーソース管理を持つ。ThunderbirdでCalDAVを設定しただけでは、そのカレンダーがEDSにも自動登録されるとは限らない。本アプリが読むのはThunderbirdのプロファイルではなく、EDSに登録されたソースである。両方から同じTODOを使う場合は、同じCalDAVサーバー／タスクリストをEDSにも登録する。

Evolutionを使う場合の一般的な登録手順:

1. Evolutionのタスク画面で **ファイル → 新規 → タスクリスト** を選ぶ。
2. 種類に **CalDAV** を選び、利用者が管理するサーバーURLと認証情報を入力する。
3. **タスクリストを検索** で対象リストを選ぶか、サーバーが要求するURLを入力して登録する。
4. タスクリストがEDSに現れることを確認する。Paper Phoneからはこのソースを選んで読むだけで、タスクの作成・編集・完了・削除は行わない。

GNOME Online Accountsで対象プロバイダーがサポートされている場合は、そこからEDSソースを登録してもよい。ThunderbirdのEDS連携アドオンは別の同期動作を追加するため、本アプリの前提・必須要件にはしない。

## 未確認事項

- 実証したEDSリモートタスクリストはCalDAV系バックエンドとして識別できたが、そのソース名はThunderbirdを示していなかった。Thunderbird側のプロファイルや設定は読まず、同じサーバー／タスクリストであることの照合もしていない。ソースの対応付けは利用者がEDS上で確認する。
- EDSの高水準GIバインディングがない環境での実証である。実装はタスク2.2でFlatpak／ホストEDSの最小権限と利用可能なAPIを確認して決める。

## 参照

- EvolutionのCalDAVタスクリスト登録: https://gnome.pages.gitlab.gnome.org/evolution/help/tasks-caldav.html
- Thunderbirdの独立したカレンダー管理: https://source-docs.thunderbird.net/en/latest/calendar/calendars.html
- ECal `GetObjectList`: https://gnome.pages.gitlab.gnome.org/evolution-data-server/libecal/method.Client.get_object_list.html
- EBook: https://gnome.pages.gitlab.gnome.org/evolution-data-server/libebook/
