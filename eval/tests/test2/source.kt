package com.pixelary.latent

import android.Manifest
import android.content.ContentValues
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.hardware.camera2.CameraCharacteristics
import android.media.Image
import android.media.ImageReader

import android.opengl.GLSurfaceView
import android.view.Surface
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.ManagedActivityResultLauncher
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.pixelary.latent.ui.CameraSelectionOverlay
import com.pixelary.latent.ui.LivePreview
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.exifinterface.media.ExifInterface
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.roundToInt
import com.pixelary.latent.ui.theme.LatentTheme
import android.view.KeyEvent
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import com.pixelary.latent.BuildConfig

class MainActivity : ComponentActivity() {
    private val volumeKeyEventFlow = MutableSharedFlow<Int>(extraBufferCapacity = 1)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enableEdgeToEdge()
        setContent {
            LatentTheme {
                MainScreen(volumeKeyEventFlow)
            }
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_UP || keyCode == KeyEvent.KEYCODE_VOLUME_DOWN) {
            // Emitting to SharedFlow is safe to do from any thread, and with extraBufferCapacity=1 it won't block.
            volumeKeyEventFlow.tryEmit(keyCode)
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    fun getRecordingReceiver(): android.content.BroadcastReceiver = recordingReceiver

    private val recordingReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: android.content.Context?, intent: android.content.Intent?) {
            android.util.Log.d("MainActivity", "Received TOGGLE_VIDEO broadcast")
            if (intent?.action == "com.pixelary.latent.TOGGLE_VIDEO") {
                volumeKeyEventFlow.tryEmit(KeyEvent.KEYCODE_VOLUME_DOWN)
            }
        }
    }
}

@Composable
fun MainScreen(volumeKeyEventFlow: SharedFlow<Int>) {
    val context = LocalContext.current
    val cameraProvider = remember { CameraProvider(context) }
    val sensorCapture = remember { SensorCapture(context) }
    
    var hasCameraPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        )
    }

    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        hasCameraPermission = permissions[Manifest.permission.CAMERA] == true &&
                              permissions[Manifest.permission.RECORD_AUDIO] == true &&
                              permissions[Manifest.permission.ACCESS_FINE_LOCATION] == true
    }

    LaunchedEffect(Unit) {
        if (!hasCameraPermission) {
            launcher.launch(arrayOf(
                Manifest.permission.CAMERA,
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION
            ))
        }
    }

    var showSettings by remember { mutableStateOf(false) }
    var cameras by remember { mutableStateOf(emptyList<CameraInfo>()) }
    var selectedCameraId by remember { mutableStateOf("") }
    var selectedPhysicalId by remember { mutableStateOf<String?>(null) }
    var zoomValue by remember { mutableFloatStateOf(1f) }
    val zoomRange = 1f..10f // Digital magnification range
    
    var aeCompensation by remember { mutableIntStateOf(0) }
    var aeCompensationRange by remember { mutableStateOf(android.util.Range(0, 0)) }
    var aeCompensationStep by remember { mutableStateOf(android.util.Rational(0, 1)) }

    var manualIso by remember { mutableIntStateOf(0) }

    var currentIsoRange by remember { mutableStateOf(android.util.Range(100, 100)) }
    var manualExposureTimeNs by remember { mutableLongStateOf(0L) }
    var currentShutterRange by remember { mutableStateOf(android.util.Range(33_333_333L, 33_333_333L)) }
    var autoLevelsMode by remember { mutableIntStateOf(1) }
    
    // Shader Stage Toggles
    var useFalseColor by remember { mutableIntStateOf(0) }
    var useSpatialDenoise by remember { mutableIntStateOf(0) } // 0: Off, 1: Cap, 2: Always
    var showHistogram by remember { mutableStateOf(true) }


    var useOis by remember { mutableStateOf(true) }
    var showDebugData by remember { mutableStateOf(false) }
    
    var currentIso by remember { mutableIntStateOf(0) }
    var currentExposureTimeNs by remember { mutableLongStateOf(0L) }
    var processingTimeMs by remember { mutableLongStateOf(0L) }
    var lastProcessingUiUpdateTime by remember { mutableLongStateOf(0L) }
    var lastMetadataUiUpdateTime by remember { mutableLongStateOf(0L) }
    var colorSaturation by remember { mutableFloatStateOf(1.0f) }
    var currentFlickerHz by remember { mutableIntStateOf(0) }
    var lockExposure by remember { mutableStateOf(false) }
    var isShowingRawPreview by remember { mutableStateOf(false) }
    var isComparisonEnabled by remember { mutableStateOf(false) }
    var highlightRecoveryEnabled by remember { mutableStateOf(true) }
    var logRange by remember { mutableFloatStateOf(500.0f) }
    var isComparisonMode by remember { mutableStateOf(false) }
    var rawPreviewSurface by remember { mutableStateOf<Surface?>(null) }


    var isRecording by remember { mutableStateOf(false) }
    var recordingStartTime by remember { mutableLongStateOf(0L) }
    var recordingDuration by remember { mutableIntStateOf(0) }
    var currentVideoFile by remember { mutableStateOf<File?>(null) }
    val captureVideo = remember { CaptureVideo() }
    val coroutineScope = rememberCoroutineScope()

    var isRecordingLocked by remember { mutableStateOf(false) }
    var lockDragOffset by remember { mutableFloatStateOf(0f) }
    val lockThreshold = 56.dp

    val glSurfaceView = remember { GLSurfaceView(context) }
    val perfOptimizer = remember { PerformanceOptimizer(context) }
    val locationTracker = remember { LocationTracker(context) }
    var activeThreads by remember { mutableStateOf(intArrayOf(android.os.Process.myTid())) }


    
    val livePreview = remember { 
        LivePreview(context).apply {
            setOnFrameProcessedListener { timeMs ->
                processingTimeMs = timeMs
                // Report actual work duration to ADPF (in nanoseconds)
                perfOptimizer.reportActualWorkDuration(timeMs * 1_000_000L)
                
                val now = System.currentTimeMillis()
                if (now - lastProcessingUiUpdateTime > 500) {
                    processingTimeMs = timeMs
                    lastProcessingUiUpdateTime = now
                }
            }
            setOnVideoThreadIdAvailableListener { tid ->
                if (!activeThreads.contains(tid)) {
                    android.os.Process.setThreadPriority(tid, android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY)
                    activeThreads = activeThreads + tid
                    perfOptimizer.updateThreads(activeThreads)
                }
            }
            setOnGlThreadIdAvailableListener { tid ->
                if (!activeThreads.contains(tid)) {
                    android.os.Process.setThreadPriority(tid, android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY)
                    activeThreads = activeThreads + tid
                    perfOptimizer.updateThreads(activeThreads)
                }
            }
            setOnIspThreadIdAvailableListener { tid ->
                if (!activeThreads.contains(tid)) {
                    android.os.Process.setThreadPriority(tid, android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY)
                    activeThreads = activeThreads + tid
                    perfOptimizer.updateThreads(activeThreads)
                }
            }
            setOnRawPreviewSurfaceReadyListener { surface ->
                rawPreviewSurface = surface
            }
            setRequestRenderCallback {
                glSurfaceView.requestRender()
            }
        }
    }

    // Background executor for burst capture processing
    val captureExecutor = remember { Executors.newFixedThreadPool(2) }
    
    // Capture the thread IDs of the capture executor and add them to ADPF session
    LaunchedEffect(captureExecutor) {
        val tids = mutableListOf<Int>()
        val count = 2
        val latch = java.util.concurrent.CountDownLatch(count)
        
        repeat(count) {
            captureExecutor.execute {
                val tid = android.os.Process.myTid()
                synchronized(tids) { tids.add(tid) }
                latch.countDown()
            }
        }
        
        // Wait for threads to report their TIDs
        withContext(kotlinx.coroutines.Dispatchers.IO) {
            latch.await()
        }
        
        if (tids.isNotEmpty()) {
            activeThreads = (activeThreads + tids).distinct().toIntArray()
            perfOptimizer.updateThreads(activeThreads)
            Log.d("MainActivity", "Capture threads added to ADPF: ${tids.joinToString()}")
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            captureExecutor.shutdown()
        }
    }

    val updatePipelineSettings = {
        livePreview.setPipelineSettings(
            useSpatialDenoise, useFalseColor, highlightRecoveryEnabled
        )
    }

    val takePhoto = {
        val shutterStart = System.currentTimeMillis()
        locationTracker.updateLocation()
        
        val metadata = CaptureMetadata(
            iso = 0, // Will be filled by GL thread
            exposureTimeNs = 0, // Will be filled by GL thread
            focalLength = sensorCapture.getFocalLength(),
            aperture = sensorCapture.getAperture(),
            location = locationTracker.getLastLocation()
        )

        livePreview.requestCapture({ byteBuffer, width, height, timestamp, readbackTime, iso, shutter, meta, recycler ->
            captureExecutor.execute {
                val finalMeta = meta?.copy(iso = iso, exposureTimeNs = shutter) ?: metadata.copy(iso = iso, exposureTimeNs = shutter)
                processCaptureInBackground(context, byteBuffer, width, height, timestamp, readbackTime, shutterStart, finalMeta)
                // Recycle the buffer after bitmap creation
                recycler()
            }
        }, metadata)
        glSurfaceView.requestRender()
    }

    val toggleVideo = {
        if (isRecording) {
            // Stop recording: Stop encoder first while EGL surface is still active, then stop render thread
            captureVideo.stopRecording()
            val droppedFrames = livePreview.stopVideoRecording()
            sensorCapture.setAwbLock(false)
            isRecording = false
            isRecordingLocked = false // Reset lock state
            lockDragOffset = 0f
            
            if (isComparisonMode) {
                livePreview.setComparisonMode(isComparisonEnabled) // Restore preview state if needed
                isComparisonMode = false
            }
            
            if (droppedFrames > 0) {
                Toast.makeText(context, "Recording finished with $droppedFrames dropped frames", Toast.LENGTH_LONG).show()
            }
            
            // Save to gallery asynchronously on a background thread to prevent Davey (UI freeze)
            currentVideoFile?.let { videoFile ->
                val loc = locationTracker.getLastLocation()
                captureExecutor.execute {
                    saveVideoToGallery(context, videoFile, loc)
                }
            }
            currentVideoFile = null
        } else {
            // Start recording
            val prefix = if (isComparisonEnabled) "LATENT_COMP_" else "LATENT_VID_"
            currentVideoFile = File(
                context.getExternalFilesDir(null),
                "${prefix}${System.currentTimeMillis()}.mp4"
            )
            currentVideoFile?.let { videoFile ->
                if (isComparisonEnabled) {
                    isComparisonMode = true
                    livePreview.setComparisonMode(true)
                }
                
                livePreview.startVideoRecording(captureVideo, videoFile, locationTracker.getLastLocation())
                // Give it a tiny bit of time to start the warm-up
                isRecording = true 
                sensorCapture.setAwbLock(true)
                recordingStartTime = System.currentTimeMillis()

                coroutineScope.launch {
                    while (isRecording) {
                        recordingDuration = ((System.currentTimeMillis() - recordingStartTime) / 1000).toInt()
                        kotlinx.coroutines.delay(1000)
                    }
                }
            }
        }
    }


    LaunchedEffect(volumeKeyEventFlow) {
        volumeKeyEventFlow.collect { keyCode ->
            if (keyCode == KeyEvent.KEYCODE_VOLUME_UP) {
                takePhoto()
            } else if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN) {
                toggleVideo()
            }
        }
    }
    
    val lifecycleOwner = LocalLifecycleOwner.current
    var lifecycleEvent by remember { mutableStateOf(Lifecycle.Event.ON_ANY) }

    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            lifecycleEvent = event
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
        }
    }

    var openedCameraId by remember { mutableStateOf("") }
    var lastOpenedSurface by remember { mutableStateOf<Surface?>(null) }

    LaunchedEffect(hasCameraPermission, selectedCameraId, selectedPhysicalId, lifecycleEvent, rawPreviewSurface) {
        if (!hasCameraPermission || selectedCameraId.isEmpty()) return@LaunchedEffect

        when (lifecycleEvent) {
            Lifecycle.Event.ON_PAUSE, Lifecycle.Event.ON_STOP -> {
                (context.findActivity() as? MainActivity)?.let { activity ->
                    try {
                        context.unregisterReceiver(activity.getRecordingReceiver())
                    } catch (e: Exception) {}
                }
                if (isRecording) {
                    captureVideo.stopRecording()
                    livePreview.stopVideoRecording()
                    isRecording = false
                    // Save to gallery asynchronously on a background thread to prevent Davey (UI freeze)
                    currentVideoFile?.let { videoFile ->
                        val loc = locationTracker.getLastLocation()
                        captureExecutor.execute {
                            saveVideoToGallery(context, videoFile, loc)
                        }
                    }
                    currentVideoFile = null
                }
                sensorCapture.closeCamera()
                livePreview.clearPendingImage()
                livePreview.cleanup()
                glSurfaceView.onPause()
                openedCameraId = ""
                lastOpenedSurface = null
                rawPreviewSurface = null // Clear the stale surface so we don't open the camera with it on resume
            }
            Lifecycle.Event.ON_RESUME, Lifecycle.Event.ON_START -> {
                (context.findActivity() as? MainActivity)?.let { activity ->
                    val filter = android.content.IntentFilter("com.pixelary.latent.TOGGLE_VIDEO")
                    ContextCompat.registerReceiver(context, activity.getRecordingReceiver(), filter, ContextCompat.RECEIVER_NOT_EXPORTED)
                }
                glSurfaceView.onResume()
                // Only open if the surface is fully initialized and ready, and not already open for this camera/surface combo
                if (rawPreviewSurface != null && (openedCameraId != "${selectedCameraId}:${selectedPhysicalId ?: ""}" || lastOpenedSurface != rawPreviewSurface)) {
                    sensorCapture.closeCamera()
                    // CRITICAL: Reset all stale state from the old camera BEFORE setting
                    // new parameters. This prevents:
                    // - Old frames being rendered with new camera parameters
                    // - Stale EGLImage texture format metadata from the wrong sensor
                    // - Auto-levels EMA values from a different lens causing brightness flicker
                    livePreview.resetForCameraSwitch()

                    sensorCapture.setOnSensorImageAvailableListener(object : SensorCapture.OnSensorImageAvailableListener {
                        override fun onSensorImageAvailable(reader: ImageReader) {
                            try {
                                val image = reader.acquireNextImage()
                                if (image != null) {
                                    livePreview.updateSensorImage(image)
                                }
                            } catch (e: Exception) {
                                Log.e("MainActivity", "live Error acquiring image", e)
                            }
                        }
                    })

                    sensorCapture.setOnMetadataAvailableListener(object : SensorCapture.OnMetadataAvailableListener {
                        override fun onMetadataAvailable(iso: Int, exposureTimeNs: Long, dynamicBlackLevel: FloatArray?, awbGains: FloatArray?, colorMatrix: FloatArray?, intrinsics: FloatArray?, flickerHz: Int) {
                            livePreview.setIso(iso)
                            livePreview.setExposureTime(exposureTimeNs)

                            // Auto-snap manual shutter if flicker detected (Keep this fast-path)
                            if (flickerHz != currentFlickerHz) {
                                currentFlickerHz = flickerHz
                                if (flickerHz > 0 && manualExposureTimeNs > 0) {
                                    val period = if (flickerHz == 50) 10_000_000L else 8_333_333L
                                    val snapped = Math.max(1L, Math.round(manualExposureTimeNs.toDouble() / period) * period)
                                    if (snapped != manualExposureTimeNs) {
                                        manualExposureTimeNs = snapped
                                        sensorCapture.setManualExposureTime(snapped)
                                    }
                                }
                            }

                            val now = System.currentTimeMillis()
                            if (now - lastMetadataUiUpdateTime > 500) {
                                currentIso = iso
                                currentExposureTimeNs = exposureTimeNs
                                lastMetadataUiUpdateTime = now
                            }
                            
                            livePreview.setBlackLevelPattern(dynamicBlackLevel ?: floatArrayOf(64f, 64f, 64f, 64f))
                            if (awbGains != null && !isRecording) {
                                livePreview.setWbGains(
                                    maxOf(0.1f, awbGains[0]),
                                    maxOf(0.1f, awbGains[1]),
                                    maxOf(0.1f, awbGains[2])
                                )
                            }

                            if (colorMatrix != null) {
                                livePreview.setColorMatrix(colorMatrix)
                            }

                        }
                    })

                    // Set all new camera parameters BEFORE opening the camera,
                    // so the first frame processed already has the correct params.
                    val effectiveId = selectedPhysicalId ?: selectedCameraId
                    val currentCameraInfo = cameras.find { it.id == selectedCameraId && (it.physicalId == selectedPhysicalId || (it.physicalId == null && selectedPhysicalId == null)) }
                    
                    val isFront = currentCameraInfo?.facing == android.hardware.camera2.CameraCharacteristics.LENS_FACING_FRONT
                    val newSensorOrientation = sensorCapture.getSensorOrientation(effectiveId)
                    val newCfaPattern = sensorCapture.getCfaPattern(effectiveId)
                    val newBlackLevel = sensorCapture.getBlackLevelPattern(effectiveId)
                    val newWhiteLevel = sensorCapture.getWhiteLevel(effectiveId, selectedCameraId)
                    val newDistortion = sensorCapture.getLensDistortion(effectiveId)

                    // Batch all LivePreview parameter updates so the ISP thread
                    // sees a consistent parameter set (avoids partial-update race)
                    livePreview.setIsMirrored(isFront)
                    livePreview.setSensorOrientation(newSensorOrientation)
                    livePreview.setCfaPattern(newCfaPattern)
                    livePreview.setBlackLevelPattern(newBlackLevel)
                    livePreview.setWhiteLevel(newWhiteLevel.toFloat())
                    livePreview.setDistortion(newDistortion)
                    livePreview.setBlackLevelOffset(0.0f)
                    livePreview.setAutoLevelsMode(autoLevelsMode)
                    livePreview.setShowHistogram(showHistogram)
                    livePreview.setLogRange(logRange)
                    livePreview.setPipelineSettings(
                        useSpatialDenoise, useFalseColor, highlightRecoveryEnabled
                    )

                    currentCameraInfo?.maxPreviewResolution?.let { size ->
                        livePreview.setRawPreviewResolution(size.width, size.height)
                    }

                    // Now open the camera — first frames will already use correct params
                    sensorCapture.openCamera(selectedCameraId, selectedPhysicalId, rawPreviewSurface)
                    openedCameraId = "${selectedCameraId}:${selectedPhysicalId ?: ""}"
                    lastOpenedSurface = rawPreviewSurface

                    currentIsoRange = sensorCapture.getIsoRange()
                     val rawShutterRange = sensorCapture.getShutterRange()
                     val maxShutterNs = 1_000_000_000L / 30
                     currentShutterRange = android.util.Range(rawShutterRange.lower, minOf(rawShutterRange.upper, maxShutterNs))
                    aeCompensationRange = sensorCapture.getExposureCompensationRange()
                    aeCompensationStep = sensorCapture.getExposureCompensationStep()
                    sensorCapture.setManualIso(manualIso)
                    sensorCapture.setManualExposureTime(manualExposureTimeNs)
                    sensorCapture.setExposureCompensation(aeCompensation)
                    sensorCapture.setAeLock(lockExposure)
                    sensorCapture.setUseOis(useOis)

                    perfOptimizer.enableSustainedPerformanceMode(context.findActivity()?.window ?: return@LaunchedEffect)
                    // Start hint session for initial threads
                    perfOptimizer.startPerformanceSession(activeThreads)
                }
            }
            else -> {}
        }
    }

    LaunchedEffect(hasCameraPermission) {
        if (hasCameraPermission) {
            cameras = cameraProvider.getAvailableCameras()
            if (cameras.isNotEmpty() && selectedCameraId.isEmpty()) {
                selectedCameraId = cameras.first().id
                selectedPhysicalId = cameras.find { it.zoomLabel == "1x" }?.physicalId ?: cameras.first().physicalId
            }
        }
    }

    Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
        BoxWithConstraints(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black)
        ) {
            val screenMaxWidth = maxWidth
            val screenMaxHeight = maxHeight
            
            val viewportHeight = screenMaxHeight * 0.9f
            val imageHeight = screenMaxWidth * (4f / 3f)
            val letterbox = (viewportHeight - imageHeight).coerceAtLeast(0.dp)
            val imageBottomFromTop = screenMaxHeight * 0.1f + letterbox * 0.1f + imageHeight

            if (hasCameraPermission) {
                // Layer 1: OpenGL Viewport with 10% black margin at the top
                Column(modifier = Modifier.fillMaxSize()) {
                    Spacer(modifier = Modifier.fillMaxHeight(0.1f))
                    AndroidView(
                        modifier = Modifier.fillMaxSize(),
                    factory = { _ ->
                        glSurfaceView.apply {
                            setEGLContextClientVersion(3)
                            setEGLConfigChooser(8, 8, 8, 8, 0, 0) // Basic 8888 config
                            // We need to set a custom chooser to include EGL_RECORDABLE_ANDROID
                            setEGLConfigChooser { egl, display ->
                                val attribList = intArrayOf(
                                    android.opengl.EGL14.EGL_RED_SIZE, 8,
                                    android.opengl.EGL14.EGL_GREEN_SIZE, 8,
                                    android.opengl.EGL14.EGL_BLUE_SIZE, 8,
                                    android.opengl.EGL14.EGL_ALPHA_SIZE, 8,
                                    android.opengl.EGL14.EGL_RENDERABLE_TYPE, android.opengl.EGL14.EGL_OPENGL_ES2_BIT,
                                    android.opengl.EGLExt.EGL_RECORDABLE_ANDROID, 1,
                                    android.opengl.EGL14.EGL_NONE
                                )
                                val configs = arrayOfNulls<javax.microedition.khronos.egl.EGLConfig>(1)
                                val numConfigs = IntArray(1)
                                egl.eglChooseConfig(display, attribList, configs, 1, numConfigs)
                                configs[0] ?: throw RuntimeException("No recordable EGL config found")
                            }
                            this.setRenderer(livePreview)
                            this.renderMode = GLSurfaceView.RENDERMODE_WHEN_DIRTY
                        }
                    }
                )
                } // End Layer 1 Column

                Column(
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .padding(top = 64.dp), 
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    val shutterSpeedText = com.pixelary.latent.ui.formatShutterSpeed(currentExposureTimeNs)
                    
                    Text(
                        text = "ISO $currentIso  |  $shutterSpeedText",
                        modifier = Modifier.padding(top = 4.dp),
                        color = Color.White,
                        style = MaterialTheme.typography.bodyMedium
                    )

                    if (isShowingRawPreview) {
                        Text(
                            text = "Google Camera",
                            modifier = Modifier.padding(top = 16.dp),
                            color = Color.White.copy(alpha = 0.5f),
                            style = MaterialTheme.typography.labelLarge
                        )
                    }
                }
                
                if (showDebugData) {
                    val batteryPower = rememberBatteryPower()
                    Column(
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .padding(top = 64.dp, end = 16.dp),
                        horizontalAlignment = Alignment.End
                    ) {
                        Text(
                            text = "${processingTimeMs}ms",
                            color = Color.Green,
                            style = MaterialTheme.typography.labelLarge
                        )
                        Text(
                            text = "${String.format("%.1f", Math.abs(batteryPower))}W",
                            color = Color.White.copy(alpha = 0.7f),
                            style = MaterialTheme.typography.labelMedium
                        )

                        ThermalStatus()
                    }
                }

                // Shutter Button & Lock Indicator Container
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 64.dp),
                    contentAlignment = Alignment.Center
                ) {
                    // Dynamic Lock Circle (Expanding background)
                    if (isRecording && !isRecordingLocked) {
                        val density = LocalDensity.current
                        val thresholdPx = with(density) { lockThreshold.toPx() }
                        // Use a smoothed progress for cleaner animation
                        val progress = (lockDragOffset / thresholdPx).coerceIn(0f, 1f)
                        
                        // Halo Base - Always visible when recording starts
                        Box(
                            modifier = Modifier
                                .size(80.dp * (1f + progress * 0.6f))
                                .background(
                                    Color.Red.copy(alpha = 0.2f + progress * 0.4f),
                                    CircleShape
                                )
                                .border(
                                    width = 2.dp,
                                    color = Color.White.copy(alpha = 0.3f + progress * 0.5f),
                                    shape = CircleShape
                                )
                        )
                    }

                    // Shutter Button
                    Box(
                        modifier = Modifier
                            .size(if (isRecording) 72.dp else 80.dp)
                            .background(
                                if (isRecording) Color.Red else Color.White,
                                CircleShape
                            )
                            .pointerInput(Unit) { // Use Unit to prevent restart on state change
                                val thresholdPx = lockThreshold.toPx()
                                awaitPointerEventScope {
                                    while (true) {
                                        val down = awaitFirstDown()
                                        
                                        // If already recording (locked or not), any new tap should stop it
                                        if (isRecording) {
                                            val up = waitForUpOrCancellation()
                                            if (up != null) {
                                                toggleVideo()
                                            }
                                            continue
                                        }

                                        // Starting new action: long-press for video, tap for photo
                                        val longPressTimeout = viewConfiguration.longPressTimeoutMillis
                                        var isVideoStarted = false
                                        
                                        val upChange = withTimeoutOrNull(longPressTimeout) {
                                            // Track movement even while waiting for long-press
                                            while (true) {
                                                val event = awaitPointerEvent()
                                                val pointer = event.changes.firstOrNull() ?: break
                                                if (pointer.pressed == false) return@withTimeoutOrNull pointer // Released
                                                
                                                // If we had logic to start video EARLY we would do it here
                                                pointer.consume()
                                            }
                                            null
                                        }

                                        if (upChange == null) {
                                            // Timeout reached -> Long press: Start Video
                                            if (!isRecording) {
                                                toggleVideo()
                                                isVideoStarted = true
                                            }

                                            // Tracking drag for lock
                                            while (true) {
                                                val event = awaitPointerEvent()
                                                val pointer = event.changes.firstOrNull()
                                                
                                                if (pointer == null || pointer.pressed == false) {
                                                    // Release pointer
                                                    if (!isRecordingLocked) {
                                                        toggleVideo()
                                                    }
                                                    lockDragOffset = 0f
                                                    break
                                                } else {
                                                    // Tracking vertical slide
                                                    val yOffset = down.position.y - pointer.position.y
                                                    if (!isRecordingLocked) {
                                                        lockDragOffset = yOffset.coerceAtLeast(0f)
                                                        if (lockDragOffset >= thresholdPx) {
                                                            isRecordingLocked = true
                                                            lockDragOffset = 0f 
                                                        }
                                                    }
                                                    pointer.consume()
                                                }
                                            }
                                        } else {
                                            // Released before timeout -> Tap: Take Photo
                                            if (!isRecording) {
                                                takePhoto()
                                            }
                                        }
                                    }
                                }
                            }
                    ) {
                        if (isRecordingLocked) {
                            Icon(
                                Icons.Default.Lock,
                                contentDescription = "Locked",
                                tint = Color.White,
                                modifier = Modifier.size(32.dp).align(Alignment.Center)
                            )
                        }
                    }
                }

                // Recording Indicator
                if (isRecording) {
                    Row(
                        modifier = Modifier
                            .align(Alignment.TopCenter)
                            .padding(top = 104.dp)
                            .background(Color.Red.copy(alpha = 0.8f), CircleShape)
                            .padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(12.dp)
                                .background(Color.White, CircleShape)
                        )
                        Text(
                            text = "REC ${recordingDuration}s",
                            color = Color.White,
                            style = MaterialTheme.typography.labelLarge
                        )
                    }
                }

                // Settings Button (Larger, Bottom Left)
                IconButton(
                    onClick = { showSettings = true },
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .padding(start = 24.dp, bottom = 64.dp)
                        .size(64.dp) // Increased size
                        .background(Color.Black.copy(alpha = 0.5f), CircleShape)
                ) {
                    Icon(
                        Icons.Default.Settings, 
                        contentDescription = "Settings", 
                        tint = Color.White,
                        modifier = Modifier.size(32.dp) // Larger icon
                    )
                }

                // Build Timer (Moved to Bottom End)
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .padding(end = 24.dp, bottom = 24.dp)
                ) {
                    BuildTimerText()
                }

                // Google Camera Preview Button (Moved to image corner, aligned with lens selector)
                Box(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(top = imageBottomFromTop - 80.dp, end = 16.dp)
                        .size(48.dp)
                        .background(Color.Black.copy(alpha = 0.5f), CircleShape)
                        .pointerInput(Unit) {
                            awaitPointerEventScope {
                                while (true) {
                                    awaitFirstDown()
                                    isShowingRawPreview = true
                                    livePreview.setShowRawPreview(true)
                                    waitForUpOrCancellation()
                                    isShowingRawPreview = false
                                    livePreview.setShowRawPreview(false)
                                }
                            }
                        },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.Compare,
                        contentDescription = "Google Camera Preview",
                        tint = Color.White,
                        modifier = Modifier.size(24.dp)
                    )
                }

                val currentMaxWidth = screenMaxWidth
                val sliderHeight = (currentMaxWidth * (4f / 3f)) * 0.25f
                Box(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(end = 16.dp, top = 40.dp)
                        .height(currentMaxWidth * (4f / 3f))
                        .width(60.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Slider(
                        value = zoomValue,
                        onValueChange = {
                            zoomValue = it
                            livePreview.setMagnification(it)
                        },
                        valueRange = zoomRange,
                        modifier = Modifier
                            .requiredWidth(sliderHeight)
                            .rotate(-90f)
                    )
                }


                Row(
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .padding(top = imageBottomFromTop - 80.dp) 
                        .background(Color.Black.copy(alpha = 0.4f), CircleShape)
                        .padding(horizontal = 8.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    // Front camera button first
                    val frontCamera = cameras.find { it.facing == android.hardware.camera2.CameraCharacteristics.LENS_FACING_FRONT }
                    if (frontCamera != null) {
                        val isSelected = selectedCameraId == frontCamera.id && selectedPhysicalId == null
                        Surface(
                            onClick = { 
                                selectedCameraId = frontCamera.id
                                selectedPhysicalId = null
                                zoomValue = 1.0f
                                livePreview.setMagnification(1.0f)
                            },
                            color = if (isSelected) MaterialTheme.colorScheme.primary else Color.Transparent,
                            shape = CircleShape,
                            modifier = Modifier.size(40.dp)
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Text(
                                    text = "F",
                                    color = if (isSelected) Color.Black else Color.White,
                                    style = MaterialTheme.typography.labelMedium
                                )
                            }
                        }
                    }

                    // Group by zoom label and show buttons
                    val physicalCameras = cameras.filter { it.isPhysical }.distinctBy { it.zoomLabel }
                    physicalCameras.forEach { cam ->
                        val isSelected = selectedPhysicalId == cam.physicalId
                        Surface(
                            onClick = { 
                                selectedCameraId = cam.id
                                selectedPhysicalId = cam.physicalId
                                // Reset digital zoom when switching physical lenses to avoid confusion
                                zoomValue = 1.0f
                                livePreview.setMagnification(1.0f)
                            },
                            color = if (isSelected) MaterialTheme.colorScheme.primary else Color.Transparent,
                            shape = CircleShape,
                            modifier = Modifier.size(40.dp)
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Text(
                                    text = cam.zoomLabel,
                                    color = if (isSelected) Color.Black else Color.White,
                                    style = MaterialTheme.typography.labelMedium
                                )
                            }
                        }
                    }
                }

                if (showSettings) {
                    CameraSelectionOverlay(
                        aeCompensation = aeCompensation,
                        aeCompensationRange = aeCompensationRange,
                        aeCompensationStep = aeCompensationStep,
                        onAeCompensationChanged = { newValue ->
                            aeCompensation = newValue
                            sensorCapture.setExposureCompensation(newValue)
                        },
                        lockExposure = lockExposure,
                        onLockExposureChanged = { 
                            lockExposure = it
                            sensorCapture.setAeLock(it)
                        },

                        manualIso = manualIso,
                        liveIso = currentIso,
                        isoRange = currentIsoRange,
                        onIsoChanged = {
                            manualIso = it
                            sensorCapture.setManualIso(it)
                        },
                        manualExposureTimeNs = manualExposureTimeNs,
                        liveExposureTimeNs = currentExposureTimeNs,
                        shutterRange = currentShutterRange,
                        onShutterChanged = {
                            manualExposureTimeNs = it
                            sensorCapture.setManualExposureTime(it)
                        },
                        autoLevelsMode = autoLevelsMode,
                        onAutoLevelsModeChanged = {
                            autoLevelsMode = it
                            livePreview.setAutoLevelsMode(it)
                        },
                        showHistogram = showHistogram,
                        onShowHistogramChanged = {
                            showHistogram = it
                            livePreview.setShowHistogram(it)
                        },
                        useFalseColor = useFalseColor,
                        onUseFalseColorChanged = {
                            useFalseColor = it
                            updatePipelineSettings()
                        },
                        useSpatialDenoise = useSpatialDenoise,
                        onUseSpatialDenoiseChanged = {
                             useSpatialDenoise = it
                             updatePipelineSettings()
                        },
                        colorSaturation = colorSaturation,
                        onColorSaturationChanged = {
                            colorSaturation = it
                            livePreview.setColorSaturation(it)
                        },

                        useOis = useOis,
                        onUseOisChanged = {
                            useOis = it
                            sensorCapture.setUseOis(it)
                        },
                        showDebugData = showDebugData,
                        onShowDebugDataChanged = {
                            showDebugData = it
                        },
                        isComparisonEnabled = isComparisonEnabled,
                        onComparisonEnabledChanged = {
                            isComparisonEnabled = it
                            livePreview.setComparisonMode(it)
                        },
                        highlightRecoveryEnabled = highlightRecoveryEnabled,
                        onHighlightRecoveryChanged = {
                            highlightRecoveryEnabled = it
                            updatePipelineSettings()
                        },
                        logRange = logRange,
                        onLogRangeChanged = {
                            logRange = it
                            livePreview.setLogRange(it)
                        },
                        onDismiss = { showSettings = false }
                    )
                }
            } else {
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        text = "Camera permission required"
                    )
                }
            }
        }
    }
}




fun processCaptureInBackground(
    context: Context,
    byteBuffer: ByteBuffer,
    width: Int,
    height: Int,
    timestamp: Long,
    readbackTime: Long,
    shutterStartTime: Long,
    metadata: CaptureMetadata
) {
    try {
        val t0 = System.currentTimeMillis()
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        byteBuffer.rewind()
        
        bitmap.copyPixelsFromBuffer(byteBuffer)
        val t1 = System.currentTimeMillis()

        saveBitmapToGallery(context, bitmap, metadata, shutterStartTime, readbackTime, t1 - t0)
        bitmap.recycle()

        val t2 = System.currentTimeMillis()
        Log.d("MainActivity", "[PERF_CAPTURE] res=${width}x${height} total=${t2 - shutterStartTime}ms wait=${t0 - shutterStartTime}ms copy=${t1 - t0}ms write=${t2 - t1}ms readback=${readbackTime}ms")
    } catch (e: Exception) {
        Log.e("MainActivity", "capture Background capture failed: ${e.message}", e)
    }
}

fun saveBitmapToGallery(
    context: Context,
    bitmap: Bitmap,
    metadata: CaptureMetadata,
    shutterStart: Long,
    readbackTime: Long,
    bitmapTime: Long
) {
    val filename = "LATENT_${metadata.timestamp}.jpg"
    val mimeType = "image/jpeg"

    val contentValues = ContentValues().apply {
        put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
        put(MediaStore.MediaColumns.MIME_TYPE, mimeType)
        put(MediaStore.MediaColumns.RELATIVE_PATH, "DCIM/Camera")
        val now = System.currentTimeMillis()
        put(MediaStore.MediaColumns.DATE_TAKEN, metadata.timestamp)
        put(MediaStore.MediaColumns.DATE_ADDED, now / 1000)
        put(MediaStore.MediaColumns.DATE_MODIFIED, now / 1000)
        put(MediaStore.MediaColumns.WIDTH, bitmap.width)
        put(MediaStore.MediaColumns.HEIGHT, bitmap.height)
        put(MediaStore.Images.ImageColumns.ORIENTATION, 0)
        put(MediaStore.MediaColumns.IS_PENDING, 1)
    }

    val uri = context.contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, contentValues)
    uri?.let { imageUri ->
        try {
            val t_start = System.currentTimeMillis()
            context.contentResolver.openOutputStream(imageUri)?.use { outputStream ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 95, outputStream)
            }
            val t_encode = System.currentTimeMillis()

            // Update EXIF metadata
            context.contentResolver.openFileDescriptor(imageUri, "rw")?.use { pfd ->
                val exif = ExifInterface(pfd.fileDescriptor)
                
                exif.setAttribute(ExifInterface.TAG_MAKE, metadata.make)
                exif.setAttribute(ExifInterface.TAG_MODEL, metadata.model)
                exif.setAttribute(ExifInterface.TAG_SOFTWARE, "Latent Camera")
                exif.setAttribute(ExifInterface.TAG_ARTIST, "Latent Camera")
                exif.setAttribute(ExifInterface.TAG_FOCAL_LENGTH, "${(metadata.focalLength * 1000).toInt()}/1000")
                exif.setAttribute(ExifInterface.TAG_F_NUMBER, metadata.aperture.toString())
                exif.setAttribute(ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY, metadata.iso.toString())
                
                val exposureTime = if (metadata.exposureTimeNs > 0) metadata.exposureTimeNs / 1_000_000_000.0 else 0.0
                
                Log.d("MainActivity", "Writing EXIF: ISO=${metadata.iso}, Exp=${exposureTime}s, Focal=${metadata.focalLength}, Aperture=${metadata.aperture}, Location=${metadata.location}")

                exif.setAttribute(ExifInterface.TAG_EXPOSURE_TIME, exposureTime.toString())

                metadata.location?.let { loc: android.location.Location ->
                    exif.setLatLong(loc.latitude, loc.longitude)
                    exif.setGpsInfo(loc)
                }

                exif.saveAttributes()
            }
            val t_exif = System.currentTimeMillis()

            contentValues.clear()
            contentValues.put(MediaStore.MediaColumns.IS_PENDING, 0)
            context.contentResolver.update(imageUri, contentValues, null, null)
            
            if (BuildConfig.DEBUG) Log.d("MainActivity", "[PERF_STILL] total=${t_exif - shutterStart}ms readback=${readbackTime}ms bitmap=${bitmapTime}ms encode=${t_encode - t_start}ms exif=${t_exif - t_encode}ms")

        } catch (e: Exception) {
            Log.e("MainActivity", "capture Failed to save image or metadata", e)
            context.mainExecutor.execute {
                Toast.makeText(context, "Failed to save image", Toast.LENGTH_SHORT).show()
            }
        }
    }
}


@Composable
fun BuildTimerText() {
    var buildTimeText by remember { mutableStateOf("Built: calculating...") }
    
    LaunchedEffect(Unit) {
        while (true) {
            val diffMs = System.currentTimeMillis() - BuildConfig.BUILD_TIME
            val diffMin = diffMs / (1000 * 60)
            buildTimeText = if (diffMin < 1) {
                "Built: just now"
            } else {
                "Built: $diffMin min ago"
            }
            kotlinx.coroutines.delay(60000)
        }
    }
    
    Surface(
        color = Color.Black.copy(alpha = 0.5f),
        shape = CircleShape
    ) {
        Text(
            text = buildTimeText,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
            color = Color.White,
            style = MaterialTheme.typography.labelMedium
        )
    }
}

fun Context.findActivity(): androidx.activity.ComponentActivity? = when (this) {
    is androidx.activity.ComponentActivity -> this
    is android.content.ContextWrapper -> baseContext.findActivity()
    else -> null
}