# 半導体工場 操業シミュレーター 設計書

## 1. 目的・スコープ

- 目的: 生産計画の検討。投入計画・ディスパッチルールを変えたときの WIP、スループット、リードタイム(サイクルタイム)、設備稼働率を比較する。
- 対象: 前工程(ウェハプロセス)。ツール群・レシピ・セットアップ・バッチ処理・再入(リエントラント)フロー・自動搬送(ストッカー/AGV/クリーンライナー)を詳細にモデル化する。後工程(組立・テスト)は対象外。
- 対象(可視化): シミュレーション結果(SQLite)を Web ブラウザで再生し、工場レイアウト上で稼働状態を見られるビューアー。Web からのシミュレーション起動も行う(第 12・13 章)。
- 非目的(初版): 最適化アルゴリズム、実データ連携、確率的な歩留まりの高度モデル、モデル(CSV)の Web 編集 GUI。

## 2. 制約

| 項目 | 内容 |
|---|---|
| 言語 | Python 3.7(標準モジュールのみ) |
| 通信 | 外部通信・CDN不可 |
| 使用モジュール | heapq, dataclasses, json, csv, argparse, random, statistics, collections, logging, unittest, sqlite3, cgi, subprocess, uuid |
| 実行環境(既定) | Windows + IIS、Python 3.7 を CGI で実行(第 13 章)。シミュレーション単体(CLI)は IIS なしでも動く |
| ビューアー | HTML/CSS/JS(素の JavaScript + Canvas)。外部ライブラリ・CDN・Web フォントは使わず、すべてローカル配置 |
| 3.7 注意 | walrus演算子、`list[int]` 形式の型ヒント、`dataclass(slots=)`、`functools.cached_property` は使用しない |
| 出力 | CLI + CSV/JSON |
| 再現性 | 乱数は `random.Random(seed)` を各所で個別に保持し、同一シード・同一入力で同一結果にする |

## 3. シミュレーション方式

離散事象シミュレーション。`heapq` によるイベントキューで時間を進める。

- 時刻単位: 分(float)。
- イベントキュー要素: `(time, priority, seq, event)`。`seq` は単調増加の連番で、同時刻イベントの順序を決定的にする。
- 同時刻の優先順位(小さいほど先): 処理完了 < 搬送完了(AGV/クリーンライナー) < 故障/復旧 < PM開始/終了 < ロット投入 < ディスパッチ判定。
- 終了条件: シミュレーション時間上限、または投入ロット全完了。
- ウォームアップ: 指定時間内の統計は集計から除外できる。

### 3.1 イベント種別

| イベント | 発生 | 処理 |
|---|---|---|
| `LotRelease` | 投入計画に従う | ロットを生成し最初の工程キューへ入れる |
| `StockerIn` | ストッカーへ格納完了 | ロットを当該エリアのツールグループ待ち行列に入れ、ディスパッチ判定を要求 |
| `ProcessEnd` | 処理完了 | 設備を解放、搬出(設備→ストッカー)の AGV タスクを要求、統計更新、ディスパッチ判定 |
| `AgvTaskEnd` | AGV タスク完了 | 搬入ならロット処理開始、搬出ならストッカー格納(または CL 搬送要求)、AGV を解放し次タスクを割当 |
| `ClArrive` | クリーンライナーのキャリアが駅に到着 | 積載ロットの荷降ろし、待機ロットの積込み |
| `BatchTimeout` | バッチ待ち上限 | 不足でもバッチ開始 |
| `EquipDown` / `EquipUp` | MTBF/MTTR | 故障開始/復旧。処理中ロットの扱いは 6.3 |
| `PMStart` / `PMEnd` | 定期保全 | 設備を計画停止/再開 |
| `Dispatch` | 上記の各契機 | 空き設備とキューを突き合わせて割当 |

## 4. ドメインモデル

すべて `dataclasses.dataclass` で定義する。

### 4.1 Lot(ロット)

- `lot_id`, `product`, `priority`(ホットロットは高), `wafers`(既定 25)
- `route_index`: 現在の工程位置
- `release_time`, `due_date`
- `history`: 各工程の `(step, equip, queue_in, start, end)`

### 4.2 Product / Route / Step(製品・工程フロー)

- Product: `name`, `route`(Step のリスト)
- Step: `step_id`, `name`, `tool_group`, `recipe`, `process_time`(分/ロット、または分/ウェハ), `process_time_dist`(固定/三角/正規切り捨て), `batch`(任意)
- 再入: 同じ `tool_group` が Route 内に複数回現れる形で表現する(例: フォトは層数ぶん再入)。
- 想定ツールグループ例: 洗浄, 成膜(CVD/PVD), フォト, エッチ, イオン注入, 拡散/炉(バッチ), CMP, 検査/測定。

### 4.3 ToolGroup / Equipment(設備)

- ToolGroup: `name`, `equipments`, `queue`(ロットの待ち行列)
- Equipment: `equip_id`, `area`, `state`(IDLE/RESERVED(搬入待ち)/BUSY/DOWN/PM/SETUP), `current_lots`, `last_recipe`, `capacity`(同時処理ロット数、バッチは複数)
- 稼働情報: `mtbf`, `mttr`(指数分布)、`pm_interval`, `pm_duration`
- 対応レシピ: `qualified_recipes`(設備ごとに処理可能なレシピ制約)

### 4.4 Setup(段取り)

- `setup_time[(from_recipe, to_recipe)]` を分で定義。同一レシピ継続は 0。

## 5. 主要ロジック

### 5.1 ディスパッチ

1. 契機: ロット到着、処理完了、復旧、PM終了、タイムアウト。
2. 対象: そのツールグループの IDLE 設備と待ちロット。
3. ルールは差し替え可能な関数(`rules.py`):
   - `FIFO`: 到着順
   - `PRIORITY`: 優先度 → 到着順
   - `EDD`: 納期が早い順
   - `SPT`: 処理時間が短い順
   - `MIN_SETUP`: 同一レシピ優先(段取り最小化)
   - `CR`(Critical Ratio): (納期−現在)/残処理時間 が小さい順
4. ルールのシグネチャ: `rule(queue, equipment, now) -> Lot or None`。選ばれたロットはキューから除去する。

### 5.2 バッチ処理(炉・洗浄など)

- `batch_min`, `batch_max`, `batch_wait_max`。
- 同一レシピのロットを `batch_max` まで集める。`batch_min` 以上かつ待ち上限到達、または `batch_max` 到達で開始する。
- 待ちタイムアウトは `BatchTimeout` イベントで実現する。

### 5.3 故障・保全

- 故障: 稼働時間ベースで次回故障時刻を指数乱数で決める。
- 処理中に故障した場合は設定で選択する。
  - `resume`: 復旧後に残り時間で再開(既定)
  - `restart`: 最初からやり直し
  - `scrap`: 初版では未対応
- PM は `pm_interval` ごとに予定する。処理中は終了を待つ設定を既定とする。

### 5.4 投入計画(Release)

- 方式: 固定間隔、ポアソン、CSV指定の日程表。
- 投入制御: `CONWIP`(WIP上限)、`FIXED`(制御なし)。
- 製品ミックスは比率で指定する。

### 5.5 自動搬送

#### 5.5.1 物理構成

```
 エリア A1                 エリア A2                 エリア A3
 [設備]..[設備]            [設備]..[設備]            [設備]..[設備]
    \ AGV /                   \ AGV /                   \ AGV /
   [ストッカー S1]           [ストッカー S2]           [ストッカー S3]
        |                         |                         |
   ==== クリーンライナー(円形・一方通行・周回キャリア複数台) ====> S1→S2→S3→S1...
```

- エリアは複数あり、各エリアにストッカー 1 基(カセット保管)、専属 AGV N 台、設備群がある。
- 1 カセット = 1 ロット。搬送・保管はすべてカセット単位。
- 設備への搬入出は必ずストッカー経由。設備→設備の直送は行わない。
- エリア間はクリーンライナー(CL)でストッカー間を搬送する。CL は円形の一方通行で、逆走はできない。

#### 5.5.2 ロットの移動フロー

1. ロット投入: 指定ストッカー(`release_stocker`)に格納される。
2. ストッカー格納 → 次工程のツールグループ待ち行列に入る(待ち行列はストッカー内、`(ツールグループ, エリア)` ごと)。
3. ディスパッチ: 設備が空き(IDLE)かつ待ちロットがあれば割当。設備は RESERVED になる。
4. AGV 搬入(ストッカー → 設備) → 処理開始(`ProcessEnd` まで BUSY)。
5. 処理完了 → AGV 搬出(設備 → 同エリアのストッカー)。
6. 次工程のツールグループが別エリアにしかない場合、CL でそのエリアのストッカーへ搬送する。同エリアにある場合は CL を使わず手順 2 へ。
7. 最終工程の `ProcessEnd` でロット完了(完了後の搬送は扱わない)。

次工程のツールグループが複数エリアにある場合の行き先エリア選択(`sim.csv` の `area_select`):
- `LEAST_LOADED`(既定): 待ちロット数 /(稼働可能設備数)が最小のエリア。同率は現在エリアを優先、さらに同率はエリア ID 昇順。
- `NEAREST`: 現在エリアを優先し、無ければ CL で最も近いエリア。

行き先エリアは搬出時(ProcessEnd 時)に確定する。

#### 5.5.3 ストッカー

- `capacity`(カセット数)を持つ。格納数 = ストッカー内のロット数(CL 積載中、AGV 搬送中は含めない)。
- 入出庫ポート: `in_ports` / `out_ports` と `port_time`。ポートごとに「次に空く時刻」を保持し、混雑時は後続が待つ。
- 満杯の場合: 格納要求は拒否され、AGV は設備側で待機(搬出時)、CL キャリアは積載したまま周回して次の周回で再試行する。拒否回数と待ち時間を記録する。
- 統計: 平均/最大格納数、満杯時間比率、拒否回数。

#### 5.5.4 AGV(エリア専属)

- エリアごとに `agv_count` 台。他エリアには移動しない。
- 搬送要求(タスク)= `(ロット, from, to)`。from/to はストッカーまたは設備。
  - 搬入: ストッカー → 設備
  - 搬出: 設備 → ストッカー
- 移動時間はスター型(ストッカーがハブ)で表現する。設備 e とストッカー間の片道時間を `agv_time(e)`、ロード/アンロード時間を `agv_load_time` / `agv_unload_time` とする。
  - ストッカー ↔ 設備: `agv_time(e)`
  - 設備 a → 設備 b(空走): `agv_time(a) + agv_time(b)`
  - タスク所要 = 空走(現在位置→from) + ロード + 実走(from→to) + アンロード
- 割当ルール(エリアの待ちタスクと空き AGV の突合せ):
  - タスク選択: 優先度 → 依頼時刻(FIFO)。搬出(設備ブロック解消)を搬入より優先する設定が可能(`agv_out_first`、既定 1)。
  - AGV 選択: 空走時間が最小の空き AGV。同率は agv_id 昇順。
- AGV は状態(IDLE/MOVING_EMPTY/CARRYING)と現在位置を保持する。待機中はその場(最後の降ろし位置)に留まる。
- 統計: 稼働率(空走・実走の別)、タスク待ち時間(依頼→AGV 到着)、タスク数。
- 簡略化(初版): AGV 間の干渉・経路競合は扱わない。AGV は 1 台 1 カセットを搬送する。

#### 5.5.5 クリーンライナー(CL)

円形一方通行のループに、駅(ストッカーとの受渡し位置)が一定の順序で並ぶ。複数のキャリア(台車)が一定速度で周回する。

- ループ定義: 駅の並び `seq` と、次の駅までの所要時間 `time_to_next`。周回時間 `T = Σ time_to_next`。
- キャリア: `cl_carriers` 台、各 `cl_slots` 個(既定 2)のカセットを積載可。AGV は 1 回 1 カセットのみ。2 スロットは独立で、行き先が異なるロットを同時に積める(別々の駅で降ろす)。同じ駅から同じキャリアへ同時に 2 ロット積む場合、積込み時間はロットごとに加算する。時刻 0 でループ上に等間隔(位相 `k × T / M`)で配置し、一定速度で周回する。キャリアの位置は解析的に計算できるため、キャリア移動をイベント化しない。
  - キャリア k が駅 s に到着する時刻は `t = base_s + phase_k + n × T`(n は整数)。`ClArrive` イベントは「そのキャリアが当該駅に積込み/荷降ろしすべき対象があるとき」のみ生成する。
- 搬送要求 `(ロット, 出発駅 so, 到着駅 sd)`:
  1. 要求を出発駅の待ち行列(優先度 → FIFO)に入れる。
  2. 出発駅 so への各キャリアの次回到着時刻を求め、スロットに空きがあるキャリアのうち最も早いものに予約する。到着時刻 + `cl_load_time` に積込み、`so → sd` の区間時間(`time_to_next` の合計、ループ一周を超えない)後に sd へ到着、`cl_unload_time` 後に降ろす。
  3. スロット予約は「キャリア k のスロット j が時刻区間 [積込み, 荷降ろし] で使用中」という区間リストで管理し、重なる予約は不可。空きが無ければ次の周回(+T)以降を探す。
  4. 到着駅のストッカーが満杯(5.5.3)の場合は積載したまま周回し、次回到着(+T)で再試行する。この間スロットは占有される。
- 同一駅では、ストッカーのポート(5.5.3)の空き時間も考慮する(CL の積込み・荷降ろしはポートを使用)。
- 搬送は FIFO の貪欲予約(要求時点で最早キャリアを確定)とする。優先度による割込みは行わない。
- 統計: キャリア稼働率(積載スロット時間/総スロット時間)、積込み待ち時間(要求→積込み)、周回待ち回数、搬送件数。
- 簡略化(初版): キャリア故障・停止、駅での追越し競合は扱わない。

#### 5.5.6 搬送時間の内訳(ロット単位で記録)

| 区分 | 定義 |
|---|---|
| `stocker_wait` | ストッカー内でのディスパッチ待ち(StockerIn → 設備予約) |
| `agv_wait` | AGV タスク依頼 → AGV の迎え到着 |
| `agv_move` | AGV 搬送中(ロード〜アンロード) |
| `cl_wait` | CL 要求 → 積込み開始 |
| `cl_move` | CL 搭載〜荷降ろし完了(周回待ちを含む) |
| `port_wait` | ストッカーポート待ち |

## 6. 入出力設計

### 6.1 入力(CSV 設定ディレクトリ)

モデル定義(機種・プロセス・レシピ・設備など)はすべて CSV で与える。1 ディレクトリ = 1 モデル。標準の `csv.DictReader` で読む。

共通ルール:
- 文字コードは `utf-8-sig`(Excel 保存の BOM 付きも可)。1 行目はヘッダ必須、列順は問わない。
- `#` で始まる行はコメント、空行は無視。
- 時間の単位はすべて分。
- 任意列は省略可(省略時は既定値)。未知の列は警告して無視する。

| ファイル | 内容 | 主キー |
|---|---|---|
| `sim.csv` | 全体パラメータ(`key,value` 形式) | key |
| `products.csv` | 機種 | product |
| `recipes.csv` | レシピ(処理内容と処理時間) | recipe |
| `routes.csv` | プロセス(機種ごとの工程フロー) | product, seq |
| `tool_groups.csv` | ツールグループ | tool_group |
| `equipments.csv` | 設備 | equip_id |
| `qualifications.csv` | 設備が処理可能なレシピ(任意) | equip_id, recipe |
| `setups.csv` | 段取り時間(任意) | tool_group, from_recipe, to_recipe |
| `release.csv` | 投入計画(`mode=schedule` のときのみ) | lot_id |
| `areas.csv` | エリア(AGV 台数・動作時間) | area |
| `stockers.csv` | ストッカー(エリアごとに 1 基) | stocker_id |
| `cleanliner.csv` | クリーンライナーの駅の並び(円形ループ) | seq |
| `layout.csv` | Web ビューアー用の描画座標(任意、11.3) | node_type, node_id |

各ファイルの列(★必須):

```
sim.csv           key,value
                    duration, warmup, seed, dispatch_rule, release_mode,
                    lots_per_day, release_control, wip_limit,
                    release_stocker, area_select, agv_out_first,
                    cl_carriers, cl_slots, cl_load_time, cl_unload_time
                    cl_*: クリーンライナーのキャリア台数/1台あたり積載数(既定 2)/積込み・荷降ろし時間

products.csv      ★product, ratio, priority, due_factor, wafers
                    ratio: 投入比率  due_factor: 納期 = 投入時刻 + 理論処理時間 × 係数

recipes.csv       ★recipe, ★tool_group, ★process_time, time_unit, dist, dist_param,
                    batch_min, batch_max, batch_wait_max
                    time_unit: lot|wafer(既定 lot)
                    dist: fixed|triangular|normal(既定 fixed)
                    dist_param: 分布の追加値(例: triangular は "min;max"、normal は標準偏差)

routes.csv        ★product, ★seq, ★recipe, step_name
                    seq の昇順が工程順。同じ recipe/tool_group が複数回出れば再入

tool_groups.csv   ★tool_group, dispatch_rule
                    dispatch_rule: 空なら sim.csv の値

equipments.csv    ★equip_id, ★tool_group, ★area, ★agv_time, capacity, ports, mtbf, mttr,
                    pm_interval, pm_duration, initial_recipe
                    agv_time: ストッカー↔設備の AGV 片道所要時間(分)

qualifications.csv ★equip_id, ★recipe
                    ファイルが無い、またはその設備の行が無い場合は
                    同一ツールグループの全レシピ可

setups.csv        ★tool_group, ★from_recipe, ★to_recipe, ★setup_time

release.csv       ★lot_id, ★product, ★release_time, priority, due_date

areas.csv         ★area, ★agv_count, agv_load_time, agv_unload_time, agv_return_home(0/1,既定1=タスク終了後ストッカーへ空走で戻る)
                    agv_count: エリア専属 AGV 台数(0 以上。0 の場合そのエリアの設備には搬送できない。
                               エリアに設備があると検証時に警告)

stockers.csv      ★stocker_id, ★area, ★capacity, in_ports, out_ports, port_time
                    1 エリアに 1 基(0 基は検証時警告=そのエリアは到達不可、
                    2 基以上はエラー)。in_ports/out_ports 既定 1、port_time 既定 0

cleanliner.csv    ★seq, ★stocker_id, ★time_to_next
                    seq 昇順にループを構成(最後の駅の次は seq 最小の駅へ戻る)
                    time_to_next: 次の駅までのキャリア走行時間(分)
```

例(抜粋):

```
# recipes.csv
recipe,tool_group,process_time,time_unit,dist,dist_param,batch_min,batch_max,batch_wait_max
R_CLEAN1,CLEAN,40,lot,fixed,,,,
R_FURN1,FURNACE,240,lot,fixed,,2,4,120
R_ETCH1,ETCH,2,wafer,triangular,1.5;3,,,

# routes.csv
product,seq,recipe,step_name
DEVICE_A,10,R_CLEAN1,初期洗浄
DEVICE_A,20,R_FURN1,酸化
DEVICE_A,30,R_ETCH1,エッチ
```

検証(起動時、全件まとめて報告し、エラーが 1 件でもあれば終了コード 2):
- 必須列の欠落、数値でない値、負値、不正な列挙値(`time_unit`、`dist`、ルール名)。
- 参照整合性: `routes.recipe` が `recipes` に存在、`recipes.tool_group` が `tool_groups` に存在、設備のツールグループが存在、`qualifications`/`setups` の設備・レシピが存在。
- 主キー重複、`routes` の `seq` 重複。
- `batch_min` > `batch_max` などの矛盾。
- 搬送: 設備の `area` が `areas` に存在、`release_stocker` が存在、`agv_count` ≥ 0、`cl_carriers` ≥ 1、`cl_slots` ≥ 1、`time_to_next` > 0、`capacity` ≥ 1。
- `cleanliner.csv` の駅が `stockers` に存在すること(存在しない `stocker_id` はエラー)、駅の重複はエラー。
- エラーメッセージは「ファイル名・行番号・列名・内容」を含める。

方針: **存在しない ID への参照(タイポ等)はエラーのまま。構造的な未接続(要素は存在するがまだ接続されていない状態)は警告に下げ、保存・実行を許す。** 未接続部分は実行時に到達不可となり、該当工程のロットは現在のストッカーで滞留するだけでクラッシュしない(行き先選択でも到達不可のエリアは候補から除外する)。警告になるもの:

- エリアにストッカーが 0 基(そのエリアへは搬送・格納できない)。2 基以上は「エリアのストッカーが一意に決まらない」ため従来どおりエラー。
- `stockers.csv` のストッカーが `cleanliner.csv` に駅を持たない(CL で到達・出発できない)。
- 各レシピに「到達可能なエリア(ストッカー+CL 駅+AGV ≥1 の経路)」にある処理可能な設備が 1 台以上あること。無い場合は警告(その工程のロットは滞留する)。従来の「全エリアに最低 1 台」検証を到達可能性ベースに読み替えたもの。
- `agv_count` = 0 のエリアに設備がある(5.5.4、前述の警告)。

### 6.2 CLI

```
python -m fabsim run --config-dir model/ --out out/ [--rule EDD] [--seed 2]
python -m fabsim compare --config-dir model/ --rules FIFO,EDD,CR --seeds 1-10 --out out/
python -m fabsim validate --config-dir model/
```

- `run` は `--out` 配下に `result.db`(SQLite、第 11 章)を出力する。`--no-db` で出力を省く。`compare` は既定で DB を出力しない(`--db` で出力)。
- `run` は `--progress-file` を受け取り、進捗(シミュレーション時刻、状態)を JSON で更新する。Web から起動する場合に使う。

- `--rule` `--seed` は `sim.csv` の値を上書きする。
- `validate` は検証のみ行い、シミュレーションは実行しない。
- 実行時に使用した設定を `out/` にコピーする(再現用)。
- `compare` は複数ルール×複数シードを順次実行し、平均と標準偏差を出力する。

### 6.3 出力

- `summary.json`: スループット(lot/日)、平均/中央値/P95 サイクルタイム、平均WIP、納期遵守率、ツールグループ別稼働率。
- `lots.csv`: ロット単位の投入・完了・サイクルタイム・納期・待ち時間合計。
- `equipment.csv`: 設備ごとの状態別時間(BUSY/IDLE/DOWN/PM/SETUP)と稼働率。
- `wip_timeseries.csv`: 一定間隔(例: 60分)の工程別/ツールグループ別 WIP。
- `transport.csv`: ロット別の搬送時間内訳(5.5.6 の各区分)と搬送回数。
- `agv.csv`: エリア・AGV 別の稼働率(空走/実走)、タスク数、平均タスク待ち時間。
- `stocker.csv`: ストッカー別の平均/最大格納数、満杯時間比率、拒否回数。
- `cleanliner_stats.csv`: キャリア別稼働率、搬送件数、平均積込み待ち時間、周回待ち回数。
- `stocker_timeseries.csv`: 一定間隔のストッカー別格納数。
- `events.log`(任意、`--trace`): イベントトレース(AGV タスク、CL 積降ろしを含む)。
- `summary.json` にも搬送指標(平均搬送時間比率、AGV/CL 稼働率、ストッカー満杯率)を含める。

## 7. 指標の定義

- サイクルタイム = 完了時刻 − 投入時刻(ウォームアップ後に完了したロットを対象)。
- スループット = 集計期間内の完了ロット数 / 期間(日)。
- WIP = 投入済み未完了ロット数。時間加重平均で算出する。
- 稼働率 = BUSY 時間 / (総時間 − PM − DOWN)と、BUSY 時間 / 総時間の両方を出力する。
- Little の法則(WIP ≒ スループット × サイクルタイム)で整合性を確認する。
- 搬送時間比率 = (agv_wait + agv_move + cl_wait + cl_move + port_wait) / サイクルタイム。
- AGV 稼働率 = (空走 + 実走時間) / (総時間 × AGV 台数)。AGV 0 台のエリアは 0 とする。
- CL キャリア稼働率 = 積載スロット時間 / (総時間 × キャリア台数 × スロット数)。
- ストッカー満杯率 = 格納数 = capacity の時間 / 総時間。

## 8. モジュール構成

```
semiconductor_factory_simulator/
  docs/design.md
  fabsim/
    __init__.py
    __main__.py      # エントリ (cli.main)
    cli.py           # argparse、run/compare
    config.py        # CSV読込・検証・dataclass化
    models.py        # Lot, Step, Product, Equipment, ToolGroup
    engine.py        # イベントキュー、時刻管理
    events.py        # イベント定義とハンドラ
    rules.py         # ディスパッチルール
    release.py       # 投入計画
    transport/
      __init__.py
      stocker.py     # ストッカー(格納数、ポート)
      agv.py         # エリア専属 AGV、タスク割当
      cleanliner.py  # 円形ループ、周回キャリア、スロット予約
    stats.py         # 統計収集、指標算出
    report.py        # CSV/JSON出力
    dbwriter.py      # SQLite 出力(スキーマ作成、バッファ書込み、区間クローズ)
    layout.py        # レイアウト座標(layout.csv 読込、無ければ自動配置)
  web/               # IIS に配置する Web アプリ(第 13 章)
    index.html
    viewer.js        # Canvas 描画、再生制御、API 呼び出し
    layoutedit.js    # レイアウト編集モード(選択・移動・整列・Undo/Redo・保存)
    viewer.css
    cgi-bin/
      api.py         # CGI エントリ(JSON API)
      apilib.py      # クエリ、パス検証、ジョブ起動(api.py から import)
      layoutstore.py # layout.csv の読書き、ロック、リビジョン、履歴、保存時検証
    web.config       # IIS ハンドラー設定(例)
  data/              # 実行時データ(IIS の公開ディレクトリの外に置く)
    models/<name>/   # モデル CSV 一式(Web から選択可能なもの)。layout.csv、.layout.lock、layout_history/ は Web から書き込まれる
    runs/<run_id>/   # result.db, job.json, stdout.log, progress.json, config/(使用した設定のコピー)
  samples/
    small_fab/       # 動作確認用の小規模モデル(CSV一式)
  tests/
    test_engine.py
    test_rules.py
    test_batch.py
    test_stats.py
    test_agv.py
    test_cleanliner.py
    test_stocker.py
    test_dbwriter.py
    test_api.py
    test_layoutstore.py
```

## 9. テスト方針

標準の `unittest` を使用する。

- 単純直列ライン(1設備、確定処理時間)でサイクルタイムが理論値と一致する。
- 同一シード・同一入力で出力が完全一致する。
- 各ディスパッチルールで選択順が期待通りになる(小さな手組みキュー)。
- バッチが `batch_min` 未満では待ち、タイムアウトで開始する。
- 故障なし・待ちなしの単純ケースで Little の法則が成立する。
- CL: 周回時間 T と位相から、キャリア k の駅 s への到着時刻が手計算と一致する。同一スロットに予約区間が重ならない。スロットが埋まると次の周回(+T)に回る。逆走しない(出発駅 → 到着駅の所要時間は常に順方向の区間和)。
- CL: 全駅が 1 周するのにちょうど T かかり、1 台・1 スロットでの 2 件同時要求は 2 件目が +T 後になる。
- AGV: 1 台・1 タスクの所要時間が `空走 + ロード + 実走 + アンロード` と一致する。複数台で空走最小の AGV が選ばれる。搬出が搬入より先に割当されること(`agv_out_first=1`)。
- ストッカー: 満杯時に格納が拒否され、CL では次周回に再試行、AGV では設備側で待機する。格納数が capacity を超えない。
- 搬送の時間内訳(`stocker_wait` 〜 `port_wait`)の合計が、処理時間を除いたロットの滞留時間と一致する。
- DB: 区間テーブルで、終了時点で `t_end` が未確定の行が残らない(全てクローズされる)。同一設備の `equipment_state` 区間が重ならず、隙間なく並ぶ。DB の集計値(稼働率・完了ロット数)が `summary.json` と一致する。
- API: 不正な `run`/`model`/パラメータ(パス区切り、`..`、範囲外の値)が拒否される。`window` の結果が手計算の区間と一致し、行数上限で打ち切られる。POST 専用の操作が GET で拒否される。
- 結果表示(`test_results.py`): WIP境界、累積/区間期間境界、状態時間の重なり・未終了区間打切り、稼働率2種、待ち平均/最大、0件の null、範囲外400、GET専用、古いDBの recipes CSV フォールバック/未対応警告を手計算と照合する。
- 結果時系列(`test_results.py`): WIP終端値、バケット/累積の処理実績・平均待ち・状態時間・稼働率、`results` との値一致、500点自動調整、IDホワイトリスト/最大6/GET専用、古いDBの待ち系列非対応を確認する。
- 構成付きレイアウト保存(`test_structure_save.py`): 設備追加/削除(qualifications カスケード)/エリア移動/エリア+ストッカー追加/検証エラー 400 で全ファイル不変/モデルロック未保持 403/両リビジョン不一致 409/履歴 20 世代/structure 無しの従来保存維持。
- モデル設定編集(`test_modelstore.py`): `model_files` の往復で内容が変わらない。検証エラー(必須列欠落・参照切れ・重複・不正 enum・負値)で保存が 400 かつファイル不変。ロック 409/期限切れ/403。base_revision 不一致 409。履歴 20 世代。clone(名前検証・重複 409・ロック非複製)。パス/`..` 拒否、GET での POST API 拒否、本文上限。
- レイアウト保存(`test_layoutstore.py`): ① ノード集合の過不足・重複・NaN・範囲外・巨大本文が 400 になる。② ロックを他クライアントが保持中なら取得が 409、期限切れなら取得できる。③ ロックを持たないクライアントの保存は 403、`base_revision` 不一致は 409 で `layout.csv` が変わらない。④ 保存で履歴が増え、21 世代目で最古が削除される。⑤ 保存が原子的(途中で例外が起きても元の `layout.csv` が残る)。⑥ `layout.csv` が一部のみ/余分な ID を含む場合に補完・無視される。⑦ BOM 付き `layout.csv` を読める。
- レイアウト編集 UI(手動チェックリスト): 移動・グリッドスナップ・範囲選択・整列/等間隔・Undo/Redo(100 ステップ)・エリア一括移動と Alt 枠のみ移動・数値入力・警告の一覧とジャンプ・Ctrl+S・未保存警告・他ユーザーのロック中は読み取り専用になること。
- CSV 設定不正(必須列欠落、参照切れ、重複、BOM付き/コメント行/空行)が正しく検出・処理され、エラーにファイル名と行番号が含まれ、終了コード 2 になる。

## 10. 実装ステップ(案)

1. models/config/engine/stats の最小構成(直列、FIFO、故障なし)
2. 再入フロー、複数設備、ディスパッチルール
3. 段取り、バッチ処理
4. 搬送(ストッカー、AGV、CL の順。まず搬送時間固定で通し、次にリソース競合を追加)
5. 故障/PM
6. 投入制御(CONWIP)、compare コマンド
7. SQLite 出力(dbwriter、`layout.csv`)
8. 【先行スパイク可】IIS 上での Python 3.7 CGI の疎通、およびシミュレーションの切り離し起動(13.5)の可否確認
9. Web API(読取り系 → 起動/キャンセル系)
10. ビューアー(レイアウト描画 → 再生 → 操作パネル)
11. レイアウト編集モード(`layoutstore.py` の読書き・ロック・検証 → 編集 API → `layoutedit.js` の移動/グリッド → 整列・Undo/Redo → 警告)
12. サンプル設定の整備、テスト拡充

## 11. シミュレーションログ(SQLite)

### 11.1 方針

- 1 回の実行 = 1 つの SQLite ファイル(`runs/<run_id>/result.db`)。標準の `sqlite3` を使用する。
- ログは「状態の区間(`t_start`〜`t_end`)」として保存する。任意時刻の状態は「`t_start <= t < t_end` の行」を引くだけで復元でき、スナップショットやイベントの再生は不要。
- 連続運動(クリーンライナーのキャリア位置)は式で決まるため保存せず、パラメータだけ保存する(5.5.5)。AGV は区間(from→to)を保存し、位置は表示側で補間する。
- 書込みはメモリ上にバッファし、`executemany` でまとめて INSERT する。シミュレーション時間で一定間隔(既定 60 分)ごとに COMMIT し、`meta.progress_time` を更新する。これにより実行中でも途中まで閲覧できる。
- 実行中は `journal_mode=WAL`、`synchronous=NORMAL`。正常終了時に `journal_mode=DELETE` へ戻し、単一ファイルにする(コピー・保管が楽)。
- DB は必ずローカルディスクに置く(WAL はネットワーク共有で動作しない)。

### 11.2 テーブル

時刻はすべてシミュレーション開始からの分(REAL)。区間が未終了の間は `t_end` が NULL。終了時に全区間を `duration` でクローズする。

| テーブル | 主な列 | 内容 |
|---|---|---|
| `meta` | key, value | `schema_version`, `run_id`, `status`(running/done/failed/cancelled), `progress_time`, `duration`, `seed`, `rule`, `started_at`, `summary_json`, CL パラメータ(`cl_carriers`, `cl_slots`, `cl_period`) |
| `areas` | area, agv_count, x, y, w, h | エリア(描画用の矩形を含む) |
| `equipments` | equip_id, area, tool_group, x, y, shape, w, h, ports | 設備と描画位置・サイズ(古い run は w=40,h=30,shape=rect,ports=1 とみなす) |
| `equip_port_lots` | equip_id, lot_id, t_start, t_end | ロットが設備ポートを占有した区間(予約〜ProcessEnd) |
| `recipes` | recipe, tool_group | 結果集計用のレシピ→ツールグループ対応(古い run は config CSV から補完) |
| `stockers` | stocker_id, area, capacity, x, y, w, h | ストッカーと描画位置・サイズ(古い run は w=60,h=40 とみなす) |
| `cl_stations` | seq, stocker_id, base_time, time_to_next | CL の駅順と、位相 0 のキャリアの駅到着時刻 |
| `lots` | lot_id, product, priority, release_time, due_date, complete_time, wafers | ロット(古い run は wafers 列なし) |
| `lot_steps` | lot_id, seq, step_name, recipe, equip_id, t_stocker_in, t_reserved, t_start, t_end | 工程ごとの履歴(ストッカー待ち〜処理終了) |
| `equip_state` | equip_id, state, lot_id, recipe, t_start, t_end | 設備の状態区間(IDLE/RESERVED/BUSY/SETUP/DOWN/PM) |
| `stocker_lots` | stocker_id, lot_id, t_start, t_end | ロットがストッカーに格納されている区間。格納数は集計で出す |
| `agv_moves` | agv_id, area, phase, from_node, to_node, lot_id, t_start, t_end | AGV の移動区間。`phase` は EMPTY/LOADED。待機は区間を作らず、直前の `to_node` に居るものとする |
| `cl_rides` | lot_id, carrier_id, slot, from_station, to_station, t_request, t_load, t_unload | CL 搭載区間(周回待ちを含む)。表示では `t_load`〜`t_unload` にキャリアへ載っているロットを描く |
| `kpi_ts` | t, metric, key, value | 一定間隔の KPI(WIP、稼働率、AGV/CL 稼働率など) |
| `events` | t, type, obj_id, lot_id, detail | イベントトレース(`--trace` のときのみ) |

- インデックス: `equip_state(t_start, t_end)`、`agv_moves(t_start, t_end)`、`cl_rides(t_load, t_unload)`、`stocker_lots(t_start, t_end)`、`lot_steps(lot_id)`、`kpi_ts(metric, t)`。
- 区間の重なり条件: `t_start < :t1 AND (t_end IS NULL OR t_end > :t0)`。
- 想定規模: 600 ロット × 300 工程 × 関連テーブル(約 5 本)で数百万行・数百 MB 以内。区間の粒度は「状態が変わる点」だけにして行数を抑える。
- スキーマは `schema_version` で管理し、ビューアーは未知のバージョンなら読込みを拒否する。

### 11.3 レイアウト(`layout.csv`、任意)

```
layout.csv   ★node_type, ★node_id, ★x, ★y, w, h
               node_type: world|area|equip|stocker
               world: キャンバス全体の大きさ(node_id=world、x=y=0、w/h=幅/高さ。既定 1000×600)
               w/h の既定: area=エリア内容に合わせて自動、equip=40×30、stocker=60×40
```

- 座標は論理単位。ビューアー側で画面にフィットさせる。設備・ストッカーの座標は絶対座標(エリア相対ではない)。
- このファイルはレイアウト編集モード(12.5)の保存先でもある。保存時は全ノードを書き出す。
- 一部のノードだけ記載がある場合、記載のないノードは自動配置で補い、記載にあってモデルに存在しない `node_id` は無視する(警告を出す)。
- run の開始時に、その時点の `layout.csv`(無ければ自動配置の結果)を `result.db` の `areas`/`equipments`/`stockers` に焼き込む。以後の編集は、既存の run の表示に影響しない。
- 無い場合は `layout.py` が自動配置する。エリアを横に並べ、設備をエリア内でグリッド配置、ストッカーをエリアの下端に置く。CL ループは全ストッカーを通る閉曲線(角丸矩形)として描く。

## 12. Web ビューアー

### 12.0 設備ポート数(14.1)

- `equipments.csv` の任意列 `ports`(整数、既定=capacity。`ports<capacity` はエラー、バッチ設備で `ports>capacity` は警告のうえ capacity に丸める)。`capacity==1` 設備は DOWN/PM 以外で `len(expected_lots)<ports` なら BUSY/SETUP/RESERVED 中でもディスパッチ対象(先行搬入)。ポート占有=予約〜そのロットの ProcessEnd。到着順の `port_queue` に待機し、設備解放(ProcessEnd/復旧/PM終)で即座に次を開始(SETUP 経由可)。`equip_port_lots` に占有区間を記録し、ビューアーは設備下辺にポート枠を描画(処理中=状態色、待機=水色)。既定(ports=capacity)では既存挙動と同一。

### 12.0 設備の描画形状

- `tool_groups.csv` に任意列 `shape`(enum: rect/round/circle/diamond/hexagon/triangle/octagon/parallelogram、検証で不正値はエラー)。空は行順 index で 8 種を循環割当(`config.resolve_shapes` が単一情報源)。
- result.db `equipments` テーブルに `shape` 列(schema_version=1 のまま。列の無い古い run は `rect` として扱う)。
- API: `layout`/`model_layout` の各設備に解決済み `shape`。モデル設定画面で shape を select 編集可(空=自動)。
- 描画は `web/shapes.js` の `shapePath()` を viewer/レイアウト編集で共有。外接矩形・クリック判定は従来どおり。凡例にツールグループ→形状を表示。

### 12.1 画面構成

```
+---------------------------------------------------------------+
| [モデル/実行選択] [ルール] [シード] [実行] [状態: running 62%]   |
+---------------------------------------------+-----------------+
|                                             | 選択中オブジェクト |
|   工場レイアウト(Canvas)                     |  設備: 状態/レシピ/|
|   エリア / 設備 / ストッカー / AGV / CL       |  ロット/待ち数     |
|                                             +-----------------+
|                                             | 現在の KPI       |
|                                             |  WIP/完了/AGV率  |
+---------------------------------------------+-----------------+
| [|<] [>/||] 速度[x1 x10 x100 x1000] 時刻 12d 03:20  ====o===== |
+---------------------------------------------------------------+
```

### 12.2 描画(初版は工場レイアウト再生のみ)

- ロットはポート枠(設備下辺の小枠、`equip_port_lots` 占有区間)内に白●で描画(処理中=枠が状態色、待機=水色)。AGV は小さな四角+輪郭で、空走・待機=灰青/搬送中=紫、積載ロットは重ねた白●。待機 AGV は設備中心から右上外側にオフセットし台ごとにずらす(設備中央の丸=ロットと誤認しない)。

| 対象 | 表現 |
|---|---|
| エリア | 矩形の枠とラベル |
| 設備 | 矩形。色は状態: BUSY=緑、IDLE=灰、RESERVED=水色、SETUP=橙、DOWN=赤、PM=黄。処理中ロット ID を表示 |
| ストッカー | 矩形。格納数 / capacity の充填ゲージ、数値、満杯時は赤枠 |
| AGV | 丸印。`agv_moves` の from→to を `t_start`〜`t_end` で線形補間。空走は白抜き、実走は塗り(ロット ID 付き)。待機中は直前の `to_node` 位置 |
| クリーンライナー | 全駅を結ぶ閉ループ(一方通行の矢印付き)。キャリアは `(位相 + 速度 × t) mod T` から位置を計算して丸印で描き、各スロットに載ったロットを小さな点で示す |
| ロット | ホットロット(優先度高)は色を変える。クリックで追跡(ハイライトと詳細表示) |

- 色は色覚多様性に配慮し、状態は色だけでなく凡例とツールチップ(文字)でも示す。
- クリックで設備・ストッカー・AGV・キャリアを選択し、右パネルに詳細(状態、現在ロット、待ち数、その時刻までの稼働率など)を表示する。
- 初版では、ロット追跡ガント、設備ガント、KPI グラフは対象外。`kpi_ts` は現在値の表示にのみ使い、グラフ化は次版。

### 12.3 再生制御・ズーム/パン

- ホイールでカーソル位置中心のズーム(fit 倍率の 0.5〜20 倍にクランプ)。ドラッグでパン(空白部左ボタン/中ボタン/Space+ドラッグ、数 px 未満はクリック判定に流しドラッグ後の誤選択は抑制)。右下オーバーレイに [+] [−] [全体] ボタンと倍率表示、ダブルクリックで全体表示。リサイズ/run 切替で fit に戻る。編集モード中(LE.on)は viewer の操作を無効化。

### 12.3 再生制御

- 再生時計はブラウザ側で持つ(`requestAnimationFrame`)。速度はシミュレーション分/実秒(1、10、100、1000)。
- 先読み: 現在時刻 `t` から `W = 速度 × 30秒` 分の窓を API で取得し、`t + W/2` に達したら次の窓を取得する。古い窓は破棄する。1 秒に 2 回を超えるリクエストを出さない(CGI は 1 リクエストごとにプロセスを起動するため)。
- シークバー操作時は、その時刻の窓を取得して直ちに描画する。
- 実行中の run: `progress_time` まで再生可能。末尾に追いついたら待機し、追いつき表示(ライブモード)に切り替えられる。完了後は全期間を再生できる。
- 窓の応答は列名と行配列を分けたコンパクトな JSON(`{"cols":[...],"rows":[[...],...]}`)とし、転送量を抑える。

### 12.5 レイアウト編集モード

- 12.5.1 の「構成変更は CSV 側のみ」は拡張: レイアウト編集画面で設備の追加/削除/エリア移動(tool_group・agv_time 変更)、エリア+ストッカーのセット追加/削除(所属設備 0 のみ、最後の1エリアは不可)、CL 駅順の入れ替え・time_to_next 変更ができる。構成変更はブラウザ内で Undo/Redo 対象として保持し、保存時に `layout_save` の `structure`(equipments/areas/stockers/cleanliner/qualifications の行群)+`base_model_revision` でレイアウトと1回にまとめて保存する。structure 付き保存はレイアウトロックとモデルロック(.model.lock)の両方所有・両リビジョン一致・編集権限を要求し、(a)現行ファイルに structure を反映(qualifications は存在しない設備の行を除去、cleanliner は存在しないストッカーの駅を除去+seq 振り直し)、(b) config.load_model 検証、(c) 新モデルに対するノード集合検証、(d) model_history+layout_history、(e) 一時書出し→連続 os.replace、の順で行う。`layout_lock` は両ロックを同時取得、`layout_unlock` は両方解放。structure 無し保存は従来どおりモデルロック不要。

#### 12.5.1 範囲と保存先

- 編集対象はモデルの配置のみ: エリア(位置・大きさ)、設備、ストッカーの位置と大きさ。設備の追加・削除、エリア所属の変更、AGV 台数、CL の駅順などの構成変更は CSV 側で行う(GUI では編集しない)。
- 保存先は `data/models/<model>/layout.csv`(11.3)。以降に開始する全 run に反映される。過去の run は自分の座標を DB に持つため変わらない。
- CL ループは編集しない。駅(ストッカー)の位置から `cleanliner.csv` の順に自動で閉曲線を描く。ストッカーを動かすとループも追随する。
- 編集モードはモデル単位で開く(ビューアーで run を閲覧中なら、その run のモデルが既定)。再生とは排他で、編集モード中は再生を停止する。

#### 12.5.2 画面

```
+---------------------------------------------------------------+
| [モデル選択] [編集ロック: 取得済 user1 あと2:40] [保存*] [破棄]  |
|  [元に戻す][やり直し] [グリッド: ON/10] [整列▼] [自動配置に戻す]  |
+---------------------------------------------+-----------------+
|                                             | 選択中           |
|   レイアウト(Canvas、グリッド表示)            |  ID/種別         |
|   エリア枠 / 設備 / ストッカー / CL ループ     |  X [  ] Y [  ]  |
|                                             |  幅 [  ] 高 [  ] |
|                                             | 警告一覧         |
+---------------------------------------------+-----------------+
```

- 設備の表示はモデル CSV(設備 ID、ツールグループ、エリア)を元にする。状態色は付けない(ツールグループ別の色分けで区別する)。
- `*` は未保存の変更あり。未保存のままページ遷移しようとしたら確認する(`beforeunload`)。

#### 12.5.3 操作

| 操作 | 内容 |
|---|---|
| 選択 | クリック、Shift+クリックで追加選択、空白ドラッグで範囲選択 |
| 移動 | ドラッグ。矢印キーで 1 グリッド、Shift+矢印で 10 グリッド移動 |
| エリアの移動 | 既定ではエリア枠と所属する設備・ストッカーを一緒に動かす。Alt を押しながらなら枠だけ動かす |
| リサイズ | エリア枠(と、設備・ストッカー)の四隅のハンドルをドラッグ |
| 数値入力 | 右パネルの X / Y / 幅 / 高さに入力して反映(精密な位置決めと、キーボードのみでの操作用) |
| グリッドスナップ | ON/OFF 切替。刻みは入力可(既定 10)。ブラウザの `localStorage` に保存 |
| 整列 | 複数選択に対し、左・右・上・下・水平中央・垂直中央に揃える。水平方向・垂直方向に等間隔配置(3 つ以上) |
| 元に戻す / やり直し | Ctrl+Z / Ctrl+Y(Ctrl+Shift+Z も可)。ブラウザ内の履歴(最大 100 ステップ)。保存しても履歴は残し、ページを閉じると消える |
| 自動配置に戻す | 全ノードを `layout.py` の自動配置結果に置き換える(確認ダイアログ付き、元に戻す対象)。 |
| ズーム / パン | ホイールでズーム、Space+ドラッグまたは中ボタンドラッグでパン |
| 保存 / 破棄 | Ctrl+S で保存。破棄は最後に保存した状態(または読み込み直後)に戻す |

- 履歴の単位: 1 回のドラッグ、1 回の整列、1 回の数値入力を 1 ステップとし、「(ノード, 変更前, 変更後)」の列として記録する。
- キャンバス外(`world` の範囲外)へは移動できない(範囲内にクランプする)。

#### 12.5.4 警告(保存は妨げない)

- 設備・ストッカーが所属エリアの枠からはみ出している。
- 同種のノード同士が重なっている(設備同士など)。
- 設備またはストッカーが他のエリアの枠の中にある。
- 最小サイズ(幅・高さ 10 未満)になっている。
- 警告は右パネルに一覧表示し、クリックで該当ノードにジャンプする。

#### 12.5.5 排他制御

同時に 2 人が編集して上書き事故が起きないよう、ロックとリビジョンの二重で防ぐ。

- 編集ロック(悲観ロック)
  - 編集開始時にロックを取得する。`data/models/<model>/.layout.lock`(JSON: `owner`、`client_id`、`expires`)を `O_CREAT|O_EXCL` で作成して取得する。
  - 有効期限 180 秒。編集中のブラウザは 60 秒ごとに更新(ハートビート)する。期限切れのロックは、他のクライアントが取得できる。
  - 他の人がロック中は、読み取り専用で開く(ロック者名と残り時間を表示)。ロック解除後に開き直せる。
  - 編集モードを閉じる際は、ロックを解放する(`beforeunload` では `sendBeacon` で試みる。失敗しても期限切れで解放される)。
  - 同じユーザーが別のタブで取得した場合も、`client_id`(ブラウザタブごとにランダム生成)が異なるため排他される。
- リビジョン確認(楽観ロック)
  - `layout.csv` の内容ハッシュ(SHA-1 の先頭 16 桁、ファイルが無ければ `none`)を `revision` とする。
  - 保存時に、読み込み時の `base_revision` と現在の `revision` が一致しなければ 409(例: ロックが期限切れの間に他の人が保存した、`layout.csv` を手で編集した)。この場合、クライアントは自分の編集を残したまま「再読込して競合を確認」を提示する。
- 保存の手順
  1. ロックの所有者が自分(`client_id`)であることを確認する。
  2. `base_revision` を確認する。
  3. 内容を検証する(12.5.6)。
  4. 現行の `layout.csv` を `data/models/<model>/layout_history/layout_YYYYmmdd_HHMMSS_<user>.csv` へコピーする(最新 20 世代を保持、古いものは削除)。
  5. 一時ファイルへ書き出して `os.replace` で置き換える(同一ボリューム内の原子的置換)。
  6. 新しい `revision` を返す。
- 保存中に開始された run は、置換の前か後かのどちらか一方の完全な `layout.csv` を読む(中途半端な内容は読まれない)。
- 履歴からの復元は、初版では手動(`layout_history` のファイルをコピー)。

#### 12.5.6 保存時の検証(サーバー側)

- 入力: ノードのリスト `{node_type, node_id, x, y, w, h}`、`client_id`、`base_revision`。本文は 256 KB まで。
- ノードの集合が、モデル CSV 上の全エリア・全設備・全ストッカーと過不足なく一致すること(未知の ID、重複、欠落は 400)。
- 数値は有限(NaN、無限大は 400)で、`0 <= x`、`x + w <= world.w`(y/h も同様)。`w`、`h` は 1〜`world` の範囲。
- 座標は小数第 2 位までに丸めて保存する。
- 編集権限: `data/web_config.json` の `layout_editors`(ユーザー名の配列。空なら認証済みの全ユーザー)に含まれること。`REMOTE_USER` が空(匿名)の場合は、`allow_anonymous_edit`(既定 false)が true のときだけ許可する。
- 書込み先は `data/models/<model>/` 配下の `layout.csv`、`.layout.lock`、`layout_history/` のみ。

### 12.4 Web からの実行

- 画面上でモデル(`data/models` 内)、ルール、シード、シミュレーション時間(上限あり)を選び「実行」する。
- 実行すると run が作られ、状態をポーリング(2〜5 秒間隔)して進捗を表示する。完了したらそのまま再生を始められる。
- 実行中の run はキャンセルできる。

### 12.6 モデル設定編集

- `modeledit.html` でモデルの CSV(レイアウト以外: sim/areas/stockers/cleanliner/equipments/tool_groups/qualifications/recipes/setups/products/routes/release)を表形式で編集する。
- タブ: 全体(sim.csv=key/value)、搬送、設備、レシピ、製品・工程、投入。行の追加・複製・削除、セル編集、routes は製品絞り込み・上下移動・seq 振り直し。
- 列メタデータは `config.py` の `CSV_SPECS`/`SIM_SPECS` が単一の情報源(必須/型/enum/参照先/既定値/ラベル)。enum・参照列は select、数値は number。
- ロック `.model.lock`(O_EXCL・180秒・60秒ハートビート。レイアウトロックとは別)、リビジョン=編集対象 CSV 全体の SHA-1 先頭16桁、履歴 `model_history/` 20世代。
- 保存はロック所有・base_revision 一致(409)・編集権限・`config.load_model` の全件検証を満たす場合のみ。警告のみなら保存可。一時ディレクトリに全 CSV を書いて検証後、各ファイルを tempfile+os.replace で置換。空になった任意ファイル(qualifications/setups/release)は削除。# コメント行は保存で失われる(UI に明記)。
- 主キー列(`ref` の参照先である列: equipments.csv の equip_id、stockers.csv の stocker_id、areas.csv の area、tool_groups.csv の tool_group、recipes.csv の recipe、products.csv の product)を編集コミットすると、クライアント側で全ファイルの参照列を旧値→新値に一括置換する(カスケードリネーム)。対象は CSV_SPECS/SIM_SPECS の ref メタデータから導出し、sim.csv の `release_stocker` のような ref 値も含む。置換件数はステータスに「参照 N 箇所を更新」と表示し、リネーム+カスケードは Undo/Redo の 1 ステップに含める(スナップショットは files とリネーム履歴を抱き合わせる)。他行の主キーと重複する新名はアラートして差し戻す(空への変更は許し、検証で必須エラーにする)。
- リネーム履歴は `{kind: 主キー列のファイル名, col: 主キー列名, old, new}` のリストとして保持し、`model_save` の `renames` パラメータで送る(保存成功・破棄でクリア)。サーバー側は kind/col を参照先の組合せに限定し、old/new は `^[A-Za-z0-9_:-]{1,64}$`・200 件までを検証する。モデル CSV の保存・検証が成功したあと、`layout.csv` が存在すれば node_id を置換して書き戻す: kind=equipments.csv なら node_type=equip の node_id と agv_point の「old:k」→「new:k」接頭辞、kind=stockers.csv なら stocker と cl_point の接頭辞、kind=areas.csv なら area。その他の kind は layout.csv に影響しない。置換は記録順に適用し、件数を `layout_renamed`・更新有無を `layout_updated` で返す(`.layout.lock` 中でも実行する=座標の参照整合を保つため)。
- 新規モデルは既存モデルの複製(model_clone)で作成し、ロック・履歴ファイルは複製しない。

### 12.7 結果表示画面

- `results.html` は run を選び、時点を1時間刻みのスライダー・日/時/分入力・±1時間ボタン・最終時点ボタンで指定する。running 中は progress_time までを選択でき、更新で再取得する。
- 集計期間は累積 `[0,t]` または区間 `[max(0,t-N),t]`。WIP は常に時点 t の瞬時値で、期間モードの影響を受けない。
- 全体タブは投入/完了累計、期間完了数、WIP(グループ待ち・設備上・搬送等)、平均 CT、設備稼働率、工程処理/待ちをカード表示。グループ/装置タブは処理実績、状態時間、待ち、稼働率を表表示し、装置はグループ/エリアで絞り込みできる。列ソートと現在表の BOM 付き CSV ダウンロードに対応。
- 表の行クリックで最大6系列を折れ線へ追加/削除。全体系列は常に選択可能。WIP(合計/待ち/設備上)、2種の稼働率、工程数・平均処理/待ち時間、状態時間(BUSY/IDLE/SETUP/DOWN/PM)、全体の完了数/CT/納期遵守率を選択し、色+線種の凡例で表示切替。刻みは1/6/24時間または任意分、点数は最大500にサーバー側調整。
- グラフはバケット毎または累積(グラフ専用)を切替。WIP は各時点の瞬時値。ホバーで時刻と各系列値、クリックで結果表の時点を同期。現在時点マーカー、BOM付きグラフCSV、高DPI/リサイズ対応。
- WIP: 未完了投入ロット数。グループ待ちは `t_stocker_in <= t < t_reserved`、設備上は `t_reserved <= t < t_end`。装置は設備上・処理中(`t_start <= t`)・搬入/段取り待ちと ports を表示する。
- DB は未完了工程の `t_end` を終了時に duration で閉じるため、選択時点が run の duration と完全一致する場合、未完了ロットの最後の工程で `t_end=duration` の行は「終了時クローズ用の番兵値」として WIP に含め、完了工程実績からは除外する。
- 期間 P の工程実績は `t_end > P_start AND t_end <= t`。処理時間は `t_end-t_start`(段取りを含まない)。待ちは `t_reserved` が P 内の工程で `t_reserved-t_stocker_in`、搬入〜処理開始は `t_start-t_reserved`。状態時間は `equip_state` 区間と P の重なりを状態別に合算し、未終了区間は t で打ち切る。稼働率は BUSY/(P時間−PM−DOWN) と BUSY/P時間の2種。
- GET `results` は result.db を `mode=ro` で読み、時点・累積/区間を検証して全体/グループ/装置集計を返す。新規 DB は `recipes(recipe,tool_group)` と `lots.wafers` を焼き込み、古い run は `config/recipes.csv` で補完する。対応表も無ければグループ帰属不能の警告、wafers 列が無ければウェハ指標を省略する。
- GET `results_series` は run/metric/scope/ids/step/mode を検証し、DB各テーブルを1回ずつ走査して時系列バケットを集計する。IDは実在するグループ/装置に限定、最大6系列。点数が500を超える場合は刻みを広げて返す。グループ対応表がない古い run ではグループ待ち等の帰属不能を警告し、該当系列を返さない。

## 13. IIS + Python 3.7 CGI(既定の実行環境)

### 13.1 構成

```
ブラウザ ── HTTP ──> IIS ──(静的)──> index.html / viewer.js / viewer.css
                       └──(CGI)───> python.exe cgi-bin/api.py  ── sqlite3 ──> data/runs/<id>/result.db
                                             └── spawn ──> python.exe -m fabsim run ...(切り離し)
```

- 静的ファイルは IIS が配信する。API だけ CGI(`api.py`)。
- `data/` は IIS の公開ディレクトリの外に置き、ブラウザから直接 DB や CSV を取得できないようにする。DB は API 経由でのみ読む。

### 13.2 IIS 設定(要点)

- 機能: IIS の「CGI」を有効化する。
- ハンドラーマッピング: `cgi-bin/*.py` → `FastCgi` ではなく CgiModule、実行ファイルは `"C:\Python37\python.exe" "%s" "%s"`(パスは環境に合わせる)。リクエスト制限は「ファイルにマップされるときのみ」、アクセスは「スクリプト」と「実行」。
- 「ISAPI および CGI の制限」で、使用する `python.exe` を許可する。
- アプリケーションプール ID に、`data/` への「変更」権限、`web/` と Python 本体への「読み取りと実行」権限を付与する。
- 認証: IIS 側で設定する(例: Windows 認証、匿名は無効)。アプリ側では独自認証を持たない。
- CGI のタイムアウト: 既定を維持する。シミュレーションは CGI リクエスト内では実行せず、必ず別プロセスで走らせる(13.5)。
- 同梱する `web.config` は、上記ハンドラーとリクエストフィルタ(`.db` `.csv` `.json` のダウンロード禁止)の設定例とする。

### 13.3 API(すべて JSON、UTF-8)

| メソッド | `action` | パラメータ | 内容 |
|---|---|---|---|
| GET | `models` | - | `data/models` のモデル名一覧 |
| GET | `runs` | - | run 一覧(run_id、モデル名、状態、開始時刻、ルール、シード) |
| POST | `start` | model, rule, seed, duration | シミュレーションを起動し run_id を返す |
| GET | `status` | run | 状態、進捗(`progress_time` / `duration`)、エラーメッセージ |
| POST | `cancel` | run | 実行中の run を停止する |
| GET | `layout` | run | エリア、設備、ストッカー、駅、CL パラメータ、`meta` |
| GET | `window` | run, t0, t1 | `[t0, t1]` と重なる区間(`equip_state` / `stocker_lots` / `agv_moves` / `cl_rides`)を列形式で返す |
| GET | `kpi` | run, t | 時刻 `t` に最も近い `kpi_ts` の値 |
| GET | `summary` | run | `meta.summary_json` |
| GET | `results` | run, t, mode=cum\|window, [window] | 時点 t と累積/直前 N 分の全体・グループ・装置指標。WIP は瞬時値 |
| GET | `results_series` | run, metric, scope, [ids], [step], mode=bucket\|cum | 時系列グラフ用の最大500点。IDは実在するグループ/装置、最大6系列 |
| GET | `model_layout` | model | 編集用のレイアウト: 全ノードの座標(`layout.csv` + 自動配置で補完)、設備のツールグループ、`world`、`revision`、ロック状態(所有者、残り時間、自分か)、`can_edit`、警告(補完したノード、無視したノード) |
| POST | `layout_lock` | model, client_id | 編集ロックの取得/更新(ハートビート)。他者が保持中なら 409(所有者と残り時間付き) |
| POST | `layout_unlock` | model, client_id | 自分のロックを解放 |
| POST | `layout_save` | model, client_id, base_revision, nodes, [structure, base_model_revision] | レイアウト(と任意で構成)を保存し、新しい `revision`/`model_revision` を返す(12.5.5, 12.5.1拡張) |
| POST | `layout_auto` | model | 自動配置の結果を返す(保存はしない)。「自動配置に戻す」用 |
| GET | `model_files` | model | モデル CSV を `{cols,rows}`、列メタデータ `spec`/`sim_spec`、`revision`、ロック状態、`can_edit` を返す |
| POST | `model_validate` | model, files | 保存せず config 検証のみ実行し、エラー/警告一覧を返す |
| POST | `model_lock` | model, client_id | モデル編集ロックの取得/更新(`.model.lock`、180秒) |
| POST | `model_unlock` | model, client_id | 自分のモデル編集ロックを解放 |
| POST | `model_save` | model, client_id, base_revision, files, [renames] | 検証・ロック・リビジョン確認のうえ CSV を保存し新 `revision` を返す(12.6)。`renames` は主キーリネーム `{kind,col,old,new}` のリストで、保存成功後に layout.csv の node_id へも適用し `layout_renamed`/`layout_updated` を返す |
| POST | `model_clone` | model, new_name | モデルを別名で複製(既存名は 409) |

- 応答の共通形式: 成功時 `{"ok":true, ...}`、失敗時 `{"ok":false,"error":"<コード>"}` と適切な HTTP ステータス(400/403/404/409/500)。
- `window` の上限: 窓の長さ(既定 最大 3 日分)と行数(既定 最大 50,000 行)を超える場合は、打ち切って `"truncated":true` を返す。

### 13.4 セキュリティ・堅牢性

- 入力検証(ホワイトリスト方式)
  - `run`: `^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$` に一致し、`data/runs/<run>` が実在すること。
  - `model`: `^[A-Za-z0-9_-]{1,64}$` に一致し、`data/models/<model>` が実在すること。パスを受け取らない。
  - `rule`: 既知のルール名のみ。`seed`: 整数(範囲制限)。`duration`: 数値で上限(既定 1 年分)以下。`t0`/`t1`: 数値。
  - 範囲外・形式不正は 400。パス区切り、`..`、NUL を含む値は 400。
- DB は `file:...?mode=ro` の URI(`sqlite3.connect(uri=True)`)で開き、SQL は固定文のみ使用する。ユーザー入力は必ずプレースホルダーに渡す。
- `start`/`cancel` は POST のみ。CSRF 対策として、カスタムヘッダー `X-Requested-With: fabsim` を必須にする。`Content-Type` は `application/json` または `application/x-www-form-urlencoded` に限定する。
- レイアウト編集 API(`layout_*`)は、書込みを `data/models/<model>/` 配下の所定ファイルに限定する。`client_id` は `^[0-9a-f]{16,32}$`。`layout_save` は本文 256 KB 以下、ロック所有・リビジョン一致・編集権限・内容検証(12.5.6)のすべてを満たす場合のみ書き込む。
- `model_save` の `renames` は `{kind,col,old,new}` のリストで 200 件まで。kind/col は ref 参照先となる既知の組合せのみ、old/new は `^[A-Za-z0-9_:-]{1,64}$` を満たすものに限定する(12.6)。
- `layout_lock` / `layout_save` の応答では、ロック所有者名(`REMOTE_USER`)以外の内部情報(パス等)を返さない。
- 同時実行数の上限(既定 2)を超える `start` は 409。上限は `data/web_config.json` で変更できる。
- エラー詳細(スタックトレース、パス)はクライアントに返さず、`data/api_error.log` に記録する。`cgitb` は有効化しない。
- 応答ヘッダー: `Cache-Control: no-store`(`runs`/`status`)、`X-Content-Type-Options: nosniff`。
- 保存データの掃除: 古い run の削除は初版では手動(運用手順に記載)。ディスク使用量が閾値を超える場合は `start` を 409 で拒否する。

### 13.5 シミュレーションの切り離し起動

CGI のリクエスト内で長時間処理すると、タイムアウトや IIS のプロセス管理で止まる。`start` は次の手順で別プロセスを起動し、すぐに応答する。

1. `run_id` を採番し、`data/runs/<run_id>/` を作成する。モデル CSV を `config/` にコピーする(再現用、以降のモデル変更の影響を受けない)。
2. `job.json` に `status=queued`、パラメータ、開始時刻を書く。
3. `subprocess.Popen([python, "-m", "fabsim", "run", ...], creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, close_fds=True, stdin/stdout/stderr をファイルへ)` で起動し、PID を `job.json` に記録して応答する。
4. シミュレーション側が `progress.json` と `meta.status` を更新する。異常終了時は `status=failed` とエラー概要を記録する。
5. `cancel` は PID の存在と `job.json` の整合を確認した上で、`taskkill /PID <pid> /T` で停止し、`status=cancelled` にする。PID の再利用による他プロセスの誤停止を避けるため、`job.json` の開始時刻とプロセスの開始時刻を照合する。
6. `status` は `job.json` と `progress.json` を読む。`running` なのに PID が存在しない場合は `failed` として扱う。

起動方式は `data/web_config.json` の `run_mode` で切り替える。
- `spawn`(既定): 上記の切り離し起動。IIS(CGI)のジョブオブジェクトの設定によっては、CGI 終了時に子プロセスも停止する場合がある。実装ステップ 8 のスパイクで確認する。
- `queue`(フォールバック): `start` は `job.json`(queued)を置くだけにする。常駐の `python -m fabsim worker --data-dir data/` がキューを監視して実行する。常駐化はタスクスケジューラ(起動時)で行う。

### 13.6 運用上の注意

- CGI は 1 リクエストごとに Python を起動するため、API の応答には数十〜百ミリ秒の固定費がかかる。ビューアーは窓単位の先読みで呼び出し回数を抑える(12.3)。
- 開発(XAMPP 等)では、IIS を使わず `python -m http.server --cgi` でも動かせるよう、`apilib.py` は環境変数(`REQUEST_METHOD`、`QUERY_STRING` など)のみに依存させる。
- Windows 上の Python 3.7 では標準出力のエンコーディングが環境依存になるため、`api.py` は `sys.stdout.buffer` に UTF-8 のバイト列を直接書く。
- パスは `os.path` で組み立て、`runs` 配下に収まっていること(`realpath` の前方一致)を毎回確認する。

## 14. 未決事項(次回確認)

- IIS の環境情報: IIS のバージョン、Python 3.7 の配置パス、認証方式(Windows 認証か)、アプリケーションプール ID、`data/` の置き場所。
- 13.5 のスパイクの結果: `spawn` が IIS(CGI)から切り離せるか。だめなら `queue` + 常駐ワーカーを標準にする。
- レイアウト座標(`layout.csv`)を実際の工場の配置に合わせて用意できるか。無い場合は自動配置で十分か。
- 想定する run のサイズ(シミュレーション期間、ロット数)。DB のサイズ・窓の上限を調整するため。
- 古い run の自動削除(保持期間)を初版に入れるか。
- ビューアーの次版候補: ロット追跡ガント、設備ガント、KPI グラフ、2 つの run の並列比較。
- 同時に閲覧するユーザー数(CGI の同時起動数に影響)。
- レイアウト編集の権限: 編集できるユーザーを `layout_editors` で限定するか(既定は認証済みの全員)。
- ロックの有効期限(既定 180 秒)とハートビート間隔(60 秒)の妥当性。
- 過去の run を、編集後の最新レイアウトで表示し直す機能が必要か(現状は run 作成時の座標のまま)。
- 履歴からの復元を GUI に出すか(現状は手動コピー)。
- `world` の既定サイズ(1000×600)と、設備・ストッカーの既定サイズ(40×30、60×40)の妥当性。実際の工場の縦横比が分かれば合わせる。

### 14.1 シミュレーションモデルの未決事項

- 設備ごとのレシピ制約(qualification)の詳細度
- 納期(due_date)の決め方(投入時刻 + 目標サイクルタイム × 係数 で良いか)
- ホットロットの比率と優先度の扱い
- 実データ(工程表・処理時間)の有無と形式
- 稼働率の既定定義(本書では 2 種類出力)
- AGV の移動時間データ(設備ごとの `agv_time`)の入手方法。スター型(ストッカーがハブ)の近似で足りるか
- CL: キャリア台数・スロット数・駅間時間・積込み/荷降ろし時間の実測値、ストッカーのポート数
- CL の駅が複数ストッカーの場合の順序、および駅の追加(分岐・バイパス)の有無
- ストッカー満杯時の実運用の挙動(拒否、別ストッカーへの退避など)
- 設備の搬出待ち(ロットが設備に残る間、設備を塞ぐか)をモデル化するか(現状は ProcessEnd で設備を解放)
- 搬送優先度(ホットロット)を AGV/CL で優先するか
- 設備ポート(ロードポート)数や、設備間の直送を許すか
