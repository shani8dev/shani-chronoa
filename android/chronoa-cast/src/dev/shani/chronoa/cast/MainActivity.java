package dev.shani.chronoa.cast;

import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.content.Context;
import android.content.Intent;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Pick this computer from the paired devices, then allow screen capture once. */
public class MainActivity extends Activity {
    private static final int REQUEST_CAPTURE = 1;
    private String target;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(32, 32, 32, 32);
        TextView title = new TextView(this);
        title.setText("Show this phone's screen on a computer, over Bluetooth.\nTap the computer:");
        title.setTextSize(18);
        list.addView(title);
        BluetoothAdapter adapter = BluetoothAdapter.getDefaultAdapter();
        if (adapter == null || !adapter.isEnabled()) {
            title.append("\n\nTurn Bluetooth on first.");
        } else {
            for (final BluetoothDevice d : adapter.getBondedDevices()) {
                Button b = new Button(this);
                b.setText(d.getName() + "\n" + d.getAddress());
                b.setOnClickListener(new View.OnClickListener() {
                    @Override public void onClick(View v) { start(d.getAddress()); }
                });
                list.addView(b);
            }
        }
        Button stop = new Button(this);
        stop.setText("Stop sharing");
        stop.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { stopService(new Intent(MainActivity.this, CastService.class)); }
        });
        list.addView(stop);
        setContentView(list);
    }

    private void start(String address) {
        target = address;
        MediaProjectionManager m = (MediaProjectionManager) getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        startActivityForResult(m.createScreenCaptureIntent(), REQUEST_CAPTURE);
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request != REQUEST_CAPTURE || result != RESULT_OK || data == null) return;
        Intent i = new Intent(this, CastService.class);
        i.putExtra("address", target);
        i.putExtra("result", result);
        i.putExtra("data", data);
        startForegroundService(i);
        finish();
    }
}
