package com.faceattend.app

import android.content.ContentResolver
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.NotFoundException
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.GlobalHistogramBinarizer
import com.google.zxing.common.HybridBinarizer

/** Finds a QR code in a picture from the gallery (a screenshot, a photo, an image sent on WhatsApp). */
object QrImage {
    private val hints = mapOf(
        DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),
        DecodeHintType.TRY_HARDER to true,
    )

    fun decode(resolver: ContentResolver, uri: Uri): String? {
        // read the size first, then load at most ~1600 px wide so big photos do not run out of memory
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 1600) sample *= 2
        val bitmap = resolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = sample })
        } ?: return null
        decode(bitmap)?.let { return it }
        // small QR in a large picture: try again at half size (helps the detector), then give up
        val half = Bitmap.createScaledBitmap(bitmap, bitmap.width / 2, bitmap.height / 2, true)
        return decode(half)
    }

    private fun decode(bitmap: Bitmap): String? {
        val pixels = IntArray(bitmap.width * bitmap.height)
        bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        val source = RGBLuminanceSource(bitmap.width, bitmap.height, pixels)
        for (binary in listOf(BinaryBitmap(HybridBinarizer(source)), BinaryBitmap(GlobalHistogramBinarizer(source)),
                              BinaryBitmap(HybridBinarizer(source.invert())))) {
            try {
                return MultiFormatReader().decode(binary, hints).text
            } catch (e: NotFoundException) {
                // try the next way of reading it
            }
        }
        return null
    }
}
