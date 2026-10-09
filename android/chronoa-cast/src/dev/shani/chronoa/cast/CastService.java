package dev.shani.chronoa.cast;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothSocket;
import android.content.Intent;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.IBinder;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.Surface;
import android.view.WindowManager;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.util.UUID;

/**
 * Screen -> H.264 (MediaCodec, input surface) -> Bluetooth RFCOMM to the computer.
 * The stream is plain Annex-B (start codes), which the computer pipes straight into
 * a decoder. Sized for a classic Bluetooth link: ~640 px wide, 8 fps, 500 kbit/s.
 */
public class CastService extends Service {
    /** Chronoa's phone-screen service on the computer (registered with bluez ProfileManager1). */
    static final UUID SERVICE = UUID.fromString("5a7e0c4a-8a2b-4c1d-9e3f-c4f0a5e5c0a5");
    private static final String TAG = "ChronoaCast";
    private volatile boolean running;
    private Thread worker;

    @Override public IBinder onBind(Intent i) { return null; }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("cast", "Screen sharing", NotificationManager.IMPORTANCE_LOW));
        Notification n = new Notification.Builder(this, "cast").setContentTitle("Sharing screen with your computer")
                .setContentText("Over Bluetooth. Open Chronoa Cast to stop.").setSmallIcon(android.R.drawable.ic_menu_share)
                .build();
        startForeground(1, n);
        final String address = intent.getStringExtra("address");
        final int result = intent.getIntExtra("result", 0);
        final Intent data = intent.getParcelableExtra("data");
        running = true;
        worker = new Thread(new Runnable() { @Override public void run() { cast(address, result, data); } });
        worker.start();
        return START_NOT_STICKY;
    }

    private void cast(String address, int result, Intent data) {
        BluetoothSocket socket = null;
        MediaProjection projection = null;
        VirtualDisplay display = null;
        MediaCodec codec = null;
        try {
            BluetoothAdapter adapter = BluetoothAdapter.getDefaultAdapter();
            adapter.cancelDiscovery();
            socket = adapter.getRemoteDevice(address).createRfcommSocketToServiceRecord(SERVICE);
            socket.connect();
            OutputStream out = socket.getOutputStream();

            DisplayMetrics dm = new DisplayMetrics();
            ((WindowManager) getSystemService(WINDOW_SERVICE)).getDefaultDisplay().getRealMetrics(dm);
            int width = 640;
            int height = ((dm.heightPixels * width / dm.widthPixels) / 16) * 16;
            MediaFormat f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
            f.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
            f.setInteger(MediaFormat.KEY_BIT_RATE, 500_000);
            f.setInteger(MediaFormat.KEY_FRAME_RATE, 8);
            f.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2);
            f.setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 250_000);
            codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
            codec.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            Surface input = codec.createInputSurface();
            codec.start();

            MediaProjectionManager m = getSystemService(MediaProjectionManager.class);
            projection = m.getMediaProjection(result, data);
            display = projection.createVirtualDisplay("ChronoaCast", width, height, dm.densityDpi,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, input, null, null);

            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            byte[] chunk = new byte[0];
            while (running) {
                int index = codec.dequeueOutputBuffer(info, 100_000);
                if (index < 0) continue;
                ByteBuffer buf = codec.getOutputBuffer(index);
                if (chunk.length < info.size) chunk = new byte[info.size];
                buf.position(info.offset);
                buf.get(chunk, 0, info.size);
                codec.releaseOutputBuffer(index, false);
                out.write(chunk, 0, info.size);
            }
        } catch (Exception e) {
            Log.w(TAG, "screen sharing stopped", e);
        } finally {
            running = false;
            try { if (display != null) display.release(); } catch (Exception ignored) { }
            try { if (codec != null) { codec.stop(); codec.release(); } } catch (Exception ignored) { }
            try { if (projection != null) projection.stop(); } catch (Exception ignored) { }
            try { if (socket != null) socket.close(); } catch (Exception ignored) { }
            stopForeground(true);
            stopSelf();
        }
    }

    @Override
    public void onDestroy() {
        running = false;
        super.onDestroy();
    }
}
