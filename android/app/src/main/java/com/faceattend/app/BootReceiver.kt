package com.faceattend.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Restart the SMS sender after the phone reboots (if it was switched on). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED && Prefs(context).let { it.gatewayEnabled && it.paired }) {
            SmsGatewayService.start(context)
        }
    }
}
