<!-- Author: T. Onkst | Date: 08112025 -->

## Process Flows

### Session Setup (WF-SESSION)
1. Select plugins
2. Import prior configuration (optional)
3. Set local data root and test folder postfix
4. Console shows plugin health (green/red)
5. Streaming starts (not recording)

### Lock and Run (WF-LOCK_START_STOP)
1. Lock Test → EngineTest metadata dialog → Pre-Test Comments
2. Start Test → begin recording at rate R (≤100 Hz)
3. Segment rollover (time/size) when thresholds exceeded → apply `_1, _2, …`
4. Stop Recording → Post-Test Comments prompt
5. Stop Test → finalize, move to final folder
6. Offer Excel export → split `.1, .2, …` if row limit exceeded
7. For new run → lock again, update metadata, start

### Error Recovery (WF-ERROR_RECOVERY)
1. On device error → retry/backoff per plugin
2. On critical fault → fail-safe stop; preserve committed data; allow export on restart

### State Machine
States: Init → Configuring → ConsoleReady → Locked → Recording → SegmentRollover → Stopping → Exporting → Completed → Error


