# Google Maps先行調査と実証の保留

## 状態

タスク2.5は未完了。利用者が用意した `.env` に `GOOGLE_MAPS_API_KEY` の値が存在し、`.env` がGit除外されていることを確認した。キーの値は出力・転記していない。今回、Google Maps APIへの認証付き要求、場所検索、経路取得、画像取得、PDF生成は行っていない。課金対象のAPI呼出しは行っていない。

## PDF／印刷条件で見つかった設計上の問題

Google Maps Platform公式FAQの「Can I create an application that includes Google Maps Platform data in a document?」は、アプリが電子または印刷文書を生成する場合、画像を含めGoogle Maps Platform由来のデータを文書に含めてはならないと説明し、利用規約のNo Scraping条項を参照している。

これは現在の設計が求める「Placesで検索し、徒歩RoutesとStatic Mapsの地図を生成PDFに掲載する」と衝突する。単に帰属を残す、個人用途とする、短期間だけ保持するという実装だけでは、アプリ生成PDFへの利用許可を確認できない。一般のGoogle Maps／Google Earthの印刷ガイドにある個人・非商用印刷の許容や一部用途の5,000部上限を、Maps Platform API経由の文書生成への例外とみなさない。

サービス固有条項では、PlacesやRoutesの座標の一時キャッシュ等の限定的な許可は確認できるが、今回のアプリ生成PDFを許可する例外は確認できていない。個別契約・地域別条項が適用される可能性については未確認であり、包括的な法的判断とはしない。

## 実装への影響

- キーの設定・APIの有効化・技術的な疎通成功だけでタスク2.5を完了にできない。
- 現在のGoogle Maps必須仕様を、黙って画面表示専用やリンクだけへ縮小しない。
- OSM等への切替は現行の対象外事項に当たるため、利用者の選択と計画変更なしには行わない。
- 利用条件と用途の整合を確認するまで、課金を伴うAPI実証とGoogle由来データのPDF組込みを保留する。
- 検索・経路・画像取得の実通信を行っていないため、新規キーの有効性、有効API、課金／割当、Legacy API非依存性、画像の帰属・解像度はまだ実証していない。

## 選択が必要な方針

1. Googleの個別許諾・契約等でアプリ生成PDFへの利用を確認し、確認できた条件に合わせてGoogle方式を続ける。
2. 印刷・PDF出力に適合する別の地図／経路提供方式へ変更する。検索・経路・背景地図・帰属・各提供サービスの利用条件を一体で再検討する。
3. 地図PDFの要件を変更し、Google連携を画面表示等に限定する。機能縮小になるため、明示的な合意と仕様変更が必要。

## 参照

- Google Maps Platform FAQ: https://developers.google.com/maps/faq （上記の文書生成に関する項目）
- Google Maps Platform Terms of Service: https://cloud.google.com/maps-platform/terms （3.2.3(a) No Scraping、3.2.3(b) No Caching等）
- Google Maps Platform Service Specific Terms: https://cloud.google.com/maps-platform/terms/maps-service-terms （Places API、Routes API等）
- 一般の地図利用・印刷ガイド: https://about.google/brand-resource-center/products-and-services/geo-guidelines/
