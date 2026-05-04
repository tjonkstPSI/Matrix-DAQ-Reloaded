<!-- Author: T. Onkst | Date: 08112025 -->

## Process Flows

### Session Setup (WF-SESSION)
1. Select plugins
2. Import prior configuration (optional)
3. Set local data root and test folder postfix
4. Launcher starts the core subprocess and waits for `core_ready`
5. Console shows plugin health (green/red)
6. Streaming starts (not recording)

### Lock and Run (WF-LOCK_START_STOP)
1. Lock Test → EngineTest metadata dialog → Pre-Test Comments
   - If the operator discovers a metadata mistake before recording, the **Unlock Test** button (visible only in the locked-and-not-recording state) reverts to the idle state after a confirmation dialog, without creating a run folder.
2. Start Recording → begin recording at rate R (≤100 Hz)
3. Segment rollover (time/size) when thresholds exceeded → apply `_1, _2, …`
4. Stop Recording → Post-Test Comments prompt → EngineTest.unlock_session() runs implicitly
5. Finalize SQLite segment database(s) under `<run>/data/seg_*.db`
6. **Auto** Excel export starts in the background and writes `Data_<run>.xlsx` (or `Data_<run>_segN.xlsx` when segmented; split `.1, .2, …` if the row limit is exceeded) into the same `<run>/data/` folder — no manual button required
7. For a new run → lock again, update metadata, start

### Error Recovery (WF-ERROR_RECOVERY)
1. On device error → retry/backoff per plugin
2. On critical fault → fail-safe stop; preserve committed data; allow export on restart

### State Machine
States: Init → Configuring → ConsoleReady → Locked → Recording → SegmentRollover → Stopping → Exporting → Completed → Error


