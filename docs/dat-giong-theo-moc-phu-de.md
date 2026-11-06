# Đặt giọng đọc theo mốc phụ đề cho `video_dub.py`

Trạng thái: Học từ Quick Share 0.1.32 (đọc tĩnh mã, 06/11/2026); chưa chạy thật
ở repo này.

Ghi chú này lấy mục "Đặt giọng đọc theo mốc phụ đề" của Quick Share, đối chiếu
với `app/video_dub.py` và ghi việc nên làm, để lồng tiếng bớt lệch phụ đề và bớt
hỏng vì edge-tts ("No audio was received"). Thông số dưới đây là của Quick
Share. Không chép mã của họ. Chưa sửa code.

## Cách Quick Share làm

Vấn đề nó giải: lồng tiếng lệch phụ đề, edge-tts chập chờn, phải canh giọng bằng
tay. Thuật toán không gắn với engine nào, chạy được với VieNeu-TTS.

1. **Đầu vào:** danh sách câu SRT `{start, end, text}`.
2. **Thẻ nghỉ** trong văn bản như `[nghỉ 1s]` hay `[pause 500ms]` được tách ra
   thành đoạn im lặng. TTS chỉ đọc phần chữ.
3. **TTS từng câu:**
   - văn bản dài được chia ở ranh giới câu, mỗi phần ≤1500 ký tự;
   - chạy song song 4 phần một lúc (8 khi làm theo mốc);
   - kiểm file trả về: đúng đầu file MP3/WAV, dung lượng có giới hạn.
4. **Đặt từng câu vào đúng `start`:**
   - câu đọc dài hơn khoảng trống tới câu sau thì tăng tốc bằng `atempo`, tối
     đa 1,5×;
   - vẫn không vừa thì đẩy lùi toàn bộ phần sau, không cho chồng câu.
5. **Khoảng lặng và cắt tính theo số mẫu âm** (`apad` / `atrim` theo sample),
   không cộng giây kiểu số thực, để không lệch dồn.
6. **Trộn cuối:** `loudnorm=I=-16:TP=-1.5:LRA=11`, mono 44,1 kHz. Repo nào đã
   có chuẩn riêng thì giữ chuẩn của repo.

## Đối chiếu với `video_dub.py` hiện tại

Luồng hiện tại:

1. `transcribe()` (`app/video_vietsub.py`) trả các câu `{start, end, zh}`.
2. `translate_batch()` thêm lời Việt `vi` cho từng câu.
3. `build_voice_track()` đọc từng câu bằng edge-tts và đặt lên một track pydub.
4. `mux()` ghép track giọng vào video, có hạ nền (ducking).

So với Quick Share:

- **Đầu vào: đã khớp.** Các câu Whisper `{start, end, vi}` chính là danh sách
  SRT.
- **Đẩy lùi, không chồng câu: đã có.** `build_voice_track` đặt câu ở
  `max(start, cursor_ms)`.
- **Tăng tốc: khác Quick Share.**
  - Câu đọc được so với ô của chính nó (`end − start`), không so với khoảng
    trống tới `start` của câu sau.
  - Trần là `MAX_SPEEDUP = 1.3`, đặt ra "để giữ dễ nghe". Quick Share cho tới
    1,5×.
- **Đơn vị thời gian: mili giây làm tròn.** Track pydub dựng theo ms
  (`int(start × 1000)`, `len(clip)`), mỗi lần nới canvas lại thêm 10 ms. Chưa
  tính theo số mẫu.
- **TTS tuần tự.** Câu này đọc xong mới tới câu sau. Văn bản không được chia nhỏ.
- **Kiểm file: chỉ kiểm file khác rỗng.**
  - `synth_segment` thử lại 4 lần, nghỉ 1,5 / 3 / 4,5 s. Lần thử được coi là
    thành công khi file tồn tại và dung lượng > 0.
  - Không kiểm đầu file MP3, không có trần dung lượng.
- **Câu hỏng bị bỏ im lặng.** Hết lượt thử thì `build_voice_track` ghi log rồi
  bỏ qua câu đó. JSON kết quả không báo câu nào thiếu tiếng.
- **Thẻ nghỉ: không có.** Lời đọc do LLM dịch, bot chưa nhận lời tự viết.
- **Loudness: repo chưa có chuẩn.** `mux()` hạ nền bằng `sidechaincompress`
  (threshold 0,03, ratio 12), trộn bằng `amix`, xuất AAC 44,1 kHz stereo, không
  chuẩn hoá loudness.
- **Chỉ có edge-tts.** Giọng chọn qua `DUB_VOICE` (mặc định
  `vi-VN-HoaiMyNeural`).

## Áp dụng vào repo này

1. **Kiểm và thử lại TTS** (`synth_segment`):
   - Coi một lần đọc là hỏng khi file không mở đầu bằng đầu MP3 (`ID3` hoặc mã
     đồng bộ khung MPEG) hay đầu WAV (`RIFF`…`WAVE`), hoặc dung lượng vượt trần.
     Xoá file hỏng rồi để vòng thử lại hiện có chạy tiếp.
   - Hết lượt thử thì chuyển sang engine dự phòng (mục 2), nếu đã bật. Nếu
     không, ghi số câu thiếu tiếng vào JSON dòng cuối cho Gami biết, thay vì bỏ
     im lặng.
2. **VieNeu làm engine thay thế:**
   - Thêm lựa chọn engine qua env (ví dụ `DUB_TTS_ENGINE=edge|vieneu`). Gami
     truyền env này giống các biến `DUB_*` khác.
   - Gọi adapter VieNeu của repo krillinai: `vieneu_server.py`,
     `POST /synthesize` nhận `{text, voice}` và trả WAV, cổng 23334, giọng mặc
     định "Ngọc Huyền".
   - Bot và VieNeu cùng chạy trên một máy Mac thì container gọi qua
     `host.docker.internal`. Theo `docs/vieneu-v3-upgrade.md` của krillinai,
     adapter mặc định nghe `0.0.0.0` cho đường này. Cần kiểm lại trên máy thật.
   - WAV của VieNeu đi chung đường kiểm ở mục 1.
3. **Đặt câu theo khoảng trống tới câu sau** (`build_voice_track`):
   - So độ dài câu đọc với khoảng từ `start` của câu này tới `start` của câu sau
     (câu cuối thì tới hết video), thay vì `end − start`.
   - Tràn thì `atempo()` tăng tốc riêng câu đó. Quick Share cho tới 1,5×, repo
     đang chặn 1,3. Đổi trần là việc chủ dự án quyết.
   - Vẫn không vừa thì đẩy lùi phần sau như hiện nay.
   - Câu bị đẩy quá cuối video sẽ bị cắt, vì `mux()` dùng `-shortest`. Cần chọn:
     kéo dài khung cuối (krillinai làm bằng `tpad=stop_mode=clone` trong
     `naturalize_dubbing.mux_video`), hoặc chấp nhận cắt.
4. **Tính theo số mẫu:**
   - Chọn một tần số mẫu cố định cho track giọng (Quick Share: mono 44,1 kHz) và
     đưa mọi câu về tần số đó.
   - Tính mốc đặt câu là `round(start × tần số)`, độ dài từng câu theo số mẫu.
     Chèn im lặng và cắt bằng `apad` / `atrim` theo sample, thay cho phép cộng
     mili giây của pydub.
5. **TTS song song:**
   - Đọc trước mọi câu song song (Quick Share: 4, hoặc 8 khi làm theo mốc), rồi
     mới đặt lên track theo thứ tự.
   - edge-tts miễn phí vốn chập chờn. Bắt đầu ở mức thấp, theo dõi tỉ lệ
     "No audio was received" rồi mới tăng.
6. **Chia câu dài:** câu dài hơn 1500 ký tự thì chia ở ranh giới câu trước khi
   đọc. Câu Whisper thường ngắn, nên đây chỉ là chặn an toàn.
7. **Thẻ nghỉ:** chỉ cần khi bot nhận lời tự viết. Khi đó tách `[nghỉ 1s]`,
   `[pause 500ms]` thành đoạn im lặng tính theo số mẫu. TTS chỉ đọc phần chữ.
8. **Loudness cuối** (`mux()`):
   - Repo chưa có chuẩn, nên dùng mức của Quick Share:
     `loudnorm=I=-16:TP=-1.5:LRA=11` sau `amix`.
   - Video vẫn xuất AAC 44,1 kHz stereo như hiện nay.
   - Nếu chủ dự án muốn theo chuẩn reel −14 LUFS thì đổi `I`.

Repo chưa có test. Khi sửa, thử trên vài video Douyin/XHS ngắn có thoại dày rồi
nghe lại.
