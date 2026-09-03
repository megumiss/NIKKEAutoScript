package com.nkas.virtualdisplay;

import java.io.BufferedReader;
import java.io.DataOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.ByteBuffer;
import java.util.UUID;

public final class Server {
    private static final Object FRAME_LOCK = new Object();

    private static final int RGBA_8888 = 1;
    private static final int THREAD_PRIORITY_URGENT_DISPLAY = -8;
    private static final int ROTATION_0 = 0;
    private static final int DISPLAY_NONE = -1;
    private static final int FRAME_BUFFER_COUNT = 3;
    private static final String DISPLAY_NAME_PREFIX = "NIKKE-";

    private static Object imageReader;
    private static Object virtualDisplay;
    private static Object captureThread;
    private static long frameCount;
    private static int frameWidth;
    private static int frameHeight;
    private static int displayRotation = ROTATION_0;
    private static int actualDisplayWidth;
    private static int actualDisplayHeight;
    private static int physicalDisplayRotation = DISPLAY_NONE;
    private static int displayId = -1;
    private static int forcedDisplaySizeId = DISPLAY_NONE;
    private static final byte[][] frameBuffers = new byte[FRAME_BUFFER_COUNT][];
    private static final int[] frameReaders = new int[FRAME_BUFFER_COUNT];
    private static int latestBuffer = DISPLAY_NONE;
    private static int latestWidth;
    private static int latestHeight;
    private static volatile boolean running = true;

    private Server() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 4) {
            throw new IllegalArgumentException("usage: width height dpi socketName");
        }
        int width = Integer.parseInt(args[0]);
        int height = Integer.parseInt(args[1]);
        int dpi = Integer.parseInt(args[2]);
        String socketName = args[3];
        frameWidth = width;
        frameHeight = height;

        Runtime.getRuntime().addShutdownHook(new Thread(Server::release));
        prepareMainLooper();
        prepareScrcpyContext();
        Object handler = createCaptureHandler();
        imageReader = createImageReader(width, height, handler);
        physicalDisplayRotation = getWindowManagerRotation();
        virtualDisplay = createVirtualDisplay(width, height, dpi, imageReader);
        Object display = virtualDisplay.getClass().getMethod("getDisplay").invoke(virtualDisplay);
        displayId = (Integer) display.getClass().getMethod("getDisplayId").invoke(display);
        updateDisplayGeometry(display, width, height);

        System.out.println("NKAS_VD_READY id=" + displayId + " socket=" + socketName
                + " size=" + actualDisplayWidth + "x" + actualDisplayHeight
                + " rotation=" + displayRotation);
        System.out.flush();
        serve(socketName);
        release();
    }

    private static void prepareMainLooper() throws Exception {
        // Android 16's ActivityThread constructor creates a Handler. app_process
        // has no prepared main Looper, unlike a normal Android application.
        Class<?> looperClass = Class.forName("android.os.Looper");
        if (looperClass.getMethod("myLooper").invoke(null) != null) {
            return;
        }
        synchronized (looperClass) {
            if (looperClass.getMethod("myLooper").invoke(null) != null) {
                return;
            }
            looperClass.getMethod("prepare").invoke(null);
            Object looper = looperClass.getMethod("myLooper").invoke(null);
            Field mainLooper = looperClass.getDeclaredField("sMainLooper");
            mainLooper.setAccessible(true);
            mainLooper.set(null, looper);
        }
    }

    private static void prepareScrcpyContext() throws Exception {
        Class<?> workarounds = Class.forName("com.genymobile.scrcpy.Workarounds");
        workarounds.getMethod("apply").invoke(null);
    }

    private static Object createCaptureHandler() throws Exception {
        Class<?> handlerThreadClass = Class.forName("android.os.HandlerThread");
        try {
            captureThread = handlerThreadClass.getConstructor(String.class, int.class)
                    .newInstance("nkas-vd-capture", THREAD_PRIORITY_URGENT_DISPLAY);
        } catch (NoSuchMethodException e) {
            captureThread = handlerThreadClass.getConstructor(String.class)
                    .newInstance("nkas-vd-capture");
        }
        handlerThreadClass.getMethod("start").invoke(captureThread);
        Object looper = handlerThreadClass.getMethod("getLooper").invoke(captureThread);
        Class<?> looperClass = Class.forName("android.os.Looper");
        Object handler = Class.forName("android.os.Handler").getConstructor(looperClass)
                .newInstance(looper);
        try {
            // HandlerThread's constructor sets the priority; this second assignment
            // mirrors MAA's guard against vendor threads changing it after startup.
            Class<?> handlerClass = Class.forName("android.os.Handler");
            handlerClass.getMethod("post", Runnable.class).invoke(handler, (Runnable) () -> {
                try {
                    Class.forName("android.os.Process").getMethod("setThreadPriority", int.class)
                            .invoke(null, THREAD_PRIORITY_URGENT_DISPLAY);
                } catch (Throwable ignored) {
                }
            });
        } catch (Throwable ignored) {
        }
        return handler;
    }

    private static Object createImageReader(int width, int height, Object handler) throws Exception {
        Class<?> readerClass = Class.forName("android.media.ImageReader");
        Object reader = null;
        Throwable usageError = null;
        try {
            Method factory = readerClass.getMethod(
                    "newInstance", int.class, int.class, int.class, int.class, long.class);
            long usage = 3L | 256L; // CPU_READ_OFTEN | GPU_SAMPLED_IMAGE
            try {
                reader = factory.invoke(null, width, height, RGBA_8888, 5, usage);
            } catch (Throwable e) {
                usageError = e;
            }
        } catch (NoSuchMethodException e) {
            // Android 9 and older only expose the four-argument factory.
        }
        if (reader == null) {
            if (usageError != null) {
                System.err.println("NKAS_VD_READER_USAGE_RETRY " + usageError);
            }
            reader = readerClass.getMethod(
                    "newInstance", int.class, int.class, int.class, int.class)
                    .invoke(null, width, height, RGBA_8888, 5);
        }

        Class<?> listenerClass = Class.forName("android.media.ImageReader$OnImageAvailableListener");
        Object listener = Proxy.newProxyInstance(
                Server.class.getClassLoader(),
                new Class<?>[]{listenerClass},
                (proxy, method, args) -> {
                    if ("onImageAvailable".equals(method.getName())) {
                        if (args != null && args.length > 0) {
                            onImageAvailable(args[0]);
                        }
                    } else if ("hashCode".equals(method.getName())) {
                        return System.identityHashCode(proxy);
                    } else if ("equals".equals(method.getName())) {
                        return proxy == args[0];
                    } else if ("toString".equals(method.getName())) {
                        return "NKAS ImageReader listener";
                    }
                    return null;
                });
        Class<?> handlerClass = Class.forName("android.os.Handler");
        readerClass.getMethod("setOnImageAvailableListener", listenerClass, handlerClass)
                .invoke(reader, listener, handler);
        return reader;
    }

    private static Object createVirtualDisplay(
            int width, int height, int dpi, Object reader) throws Exception {
        Object context = Class.forName("com.genymobile.scrcpy.FakeContext")
                .getMethod("get").invoke(null);
        Class<?> contextClass = Class.forName("android.content.Context");
        Class<?> dmClass = Class.forName("android.hardware.display.DisplayManager");
        Constructor<?> constructor = dmClass.getDeclaredConstructor(contextClass);
        constructor.setAccessible(true);
        Object manager = constructor.newInstance(context);
        Object surface = imageReader.getClass().getMethod("getSurface").invoke(reader);
        Class<?> surfaceClass = Class.forName("android.view.Surface");

        int baseFlags = dmClass.getField("VIRTUAL_DISPLAY_FLAG_PUBLIC").getInt(null)
                | dmClass.getField("VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY").getInt(null)
                | (1 << 6)  // SUPPORTS_TOUCH
                | (1 << 8); // DESTROY_CONTENT_ON_REMOVAL
        int sdk = Class.forName("android.os.Build$VERSION").getField("SDK_INT").getInt(null);
        int fullFlags = baseFlags;
        if (sdk >= 33) {
            fullFlags |= (1 << 10) // TRUSTED
                    | (1 << 11)    // OWN_DISPLAY_GROUP
                    | (1 << 12)    // ALWAYS_UNLOCKED
                    | (1 << 13);   // TOUCH_FEEDBACK_DISABLED
            if (sdk >= 34) {
                fullFlags |= (1 << 14) // OWN_FOCUS
                        | (1 << 15)     // DEVICE_DISPLAY_GROUP
                        | (1 << 16);    // STEAL_TOP_FOCUS_DISABLED
            }
        }

        Method create = dmClass.getMethod(
                "createVirtualDisplay",
                String.class, int.class, int.class, int.class,
                surfaceClass, int.class);
        int[] candidates = sdk >= 34
                ? new int[]{fullFlags, fullFlags & ~(1 << 16), baseFlags}
                : new int[]{fullFlags, baseFlags};
        Throwable lastError = null;
        int previous = -1;
        for (int flags : candidates) {
            if (flags == previous) {
                continue;
            }
            previous = flags;
            try {
                Object display = create.invoke(manager, virtualDisplayName(), width, height, dpi, surface, flags);
                if (display != null) {
                    System.out.println("NKAS_VD_FLAGS sdk=" + sdk + " flags=0x"
                            + Integer.toHexString(flags));
                    return display;
                }
            } catch (Throwable e) {
                lastError = e;
                System.err.println("NKAS_VD_FLAG_RETRY flags=0x"
                        + Integer.toHexString(flags) + " error=" + e);
            }
        }
        throw new RuntimeException("Could not create virtual display", lastError);
    }

    private static String virtualDisplayName() {
        return DISPLAY_NAME_PREFIX + UUID.randomUUID().toString().replace("-", "").substring(0, 6);
    }

    private static void updateDisplayGeometry(Object display, int fallbackWidth, int fallbackHeight) {
        actualDisplayWidth = readInt(display, "getWidth", fallbackWidth);
        actualDisplayHeight = readInt(display, "getHeight", fallbackHeight);
        displayRotation = readInt(display, "getRotation", ROTATION_0);
        if (displayRotation != ROTATION_0) {
            if (freezeDisplayRotation(displayId, ROTATION_0)) {
                actualDisplayWidth = readInt(display, "getWidth", actualDisplayWidth);
                actualDisplayHeight = readInt(display, "getHeight", actualDisplayHeight);
                displayRotation = readInt(display, "getRotation", displayRotation);
            }
            if (physicalDisplayRotation == ROTATION_0) {
                // Landscape-native devices sometimes ignore freezeRotation for a
                // secondary display. Force the configured logical size as MAA-Meow does.
                if (setForcedDisplaySize(displayId, fallbackWidth, fallbackHeight)) {
                    forcedDisplaySizeId = displayId;
                    actualDisplayWidth = readInt(display, "getWidth", fallbackWidth);
                    actualDisplayHeight = readInt(display, "getHeight", fallbackHeight);
                }
            }
        }
        if (actualDisplayWidth <= 0) {
            actualDisplayWidth = fallbackWidth;
        }
        if (actualDisplayHeight <= 0) {
            actualDisplayHeight = fallbackHeight;
        }
        System.out.println("NKAS_VD_GEOMETRY physicalRotation=" + physicalDisplayRotation
                + " displayRotation=" + displayRotation + " size="
                + actualDisplayWidth + "x" + actualDisplayHeight);
    }

    private static int readInt(Object object, String methodName, int fallback) {
        if (object == null) {
            return fallback;
        }
        try {
            Object value = object.getClass().getMethod(methodName).invoke(object);
            return value instanceof Number ? ((Number) value).intValue() : fallback;
        } catch (Throwable ignored) {
            return fallback;
        }
    }

    private static Object getServiceBinder(String name) {
        try {
            Class<?> serviceManager = Class.forName("android.os.ServiceManager");
            Method method = serviceManager.getDeclaredMethod("getService", String.class);
            method.setAccessible(true);
            return method.invoke(null, name);
        } catch (Throwable e) {
            System.err.println("NKAS_VD_SERVICE_ERROR " + name + " " + e);
            return null;
        }
    }

    private static Object getWindowManagerProxy() {
        try {
            Object binder = getServiceBinder("window");
            if (binder == null) {
                return null;
            }
            Class<?> stub = Class.forName("android.view.IWindowManager$Stub");
            Class<?> binderClass = Class.forName("android.os.IBinder");
            return stub.getMethod("asInterface", binderClass).invoke(null, binder);
        } catch (Throwable e) {
            System.err.println("NKAS_VD_WINDOW_MANAGER_ERROR " + e);
            return null;
        }
    }

    private static int getWindowManagerRotation() {
        Object manager = getWindowManagerProxy();
        if (manager == null) {
            return DISPLAY_NONE;
        }
        for (String name : new String[]{"getDefaultDisplayRotation", "getRotation"}) {
            try {
                Object value = manager.getClass().getMethod(name).invoke(manager);
                if (value instanceof Number) {
                    return ((Number) value).intValue();
                }
            } catch (Throwable ignored) {
            }
        }
        return DISPLAY_NONE;
    }

    private static boolean freezeDisplayRotation(int id, int rotation) {
        Object manager = getWindowManagerProxy();
        if (manager == null) {
            return false;
        }
        try {
            Method method = manager.getClass().getMethod(
                    "freezeDisplayRotation", int.class, int.class, String.class);
            method.invoke(manager, id, rotation, "nkas#freezeRotation");
            return true;
        } catch (Throwable ignored) {
        }
        try {
            Method method = manager.getClass().getMethod(
                    "freezeDisplayRotation", int.class, int.class);
            method.invoke(manager, id, rotation);
            return true;
        } catch (Throwable ignored) {
        }
        if (id == 0) {
            try {
                Method method = manager.getClass().getMethod("freezeRotation", int.class);
                method.invoke(manager, rotation);
                return true;
            } catch (Throwable ignored) {
            }
        }
        System.err.println("NKAS_VD_ROTATION_UNSUPPORTED id=" + id);
        return false;
    }

    private static boolean setForcedDisplaySize(int id, int width, int height) {
        Object manager = getWindowManagerProxy();
        if (manager == null) {
            return false;
        }
        try {
            Method method = manager.getClass().getMethod(
                    "setForcedDisplaySize", int.class, int.class, int.class);
            method.invoke(manager, id, width, height);
            System.out.println("NKAS_VD_FORCED_SIZE id=" + id + " size=" + width + "x" + height);
            return true;
        } catch (Throwable e) {
            System.err.println("NKAS_VD_FORCED_SIZE_ERROR " + e);
            return false;
        }
    }

    private static void clearForcedDisplaySize() {
        int id = forcedDisplaySizeId;
        forcedDisplaySizeId = DISPLAY_NONE;
        if (id == DISPLAY_NONE) {
            return;
        }
        Object manager = getWindowManagerProxy();
        if (manager == null) {
            return;
        }
        try {
            Method method = manager.getClass().getMethod("clearForcedDisplaySize", int.class);
            method.invoke(manager, id);
        } catch (Throwable e) {
            System.err.println("NKAS_VD_CLEAR_FORCED_SIZE_ERROR " + e);
        }
    }

    private static Object getFakeContext() {
        try {
            return Class.forName("com.genymobile.scrcpy.FakeContext")
                    .getMethod("get").invoke(null);
        } catch (Throwable e) {
            System.err.println("NKAS_VD_CONTEXT_ERROR " + e);
            return null;
        }
    }

    private static Object getActivityManagerProxy() {
        try {
            Class<?> nativeClass = Class.forName("android.app.ActivityManagerNative");
            return nativeClass.getDeclaredMethod("getDefault").invoke(null);
        } catch (Throwable ignored) {
        }
        try {
            Class<?> activityManager = Class.forName("android.app.ActivityManager");
            return activityManager.getDeclaredMethod("getService").invoke(null);
        } catch (Throwable ignored) {
        }
        try {
            Object binder = getServiceBinder("activity");
            Class<?> stub = Class.forName("android.app.IActivityManager$Stub");
            Class<?> binderClass = Class.forName("android.os.IBinder");
            return stub.getMethod("asInterface", binderClass).invoke(null, binder);
        } catch (Throwable e) {
            System.err.println("NKAS_VD_ACTIVITY_MANAGER_ERROR " + e);
            return null;
        }
    }

    private static Object getActivityTaskManagerProxy() {
        try {
            Object binder = getServiceBinder("activity_task");
            if (binder == null) {
                return null;
            }
            Class<?> stub = Class.forName("android.app.IActivityTaskManager$Stub");
            Class<?> binderClass = Class.forName("android.os.IBinder");
            return stub.getMethod("asInterface", binderClass).invoke(null, binder);
        } catch (Throwable e) {
            System.err.println("NKAS_VD_ACTIVITY_TASK_MANAGER_ERROR " + e);
            return null;
        }
    }

    private static Object getField(Object object, String name) {
        if (object == null) {
            return null;
        }
        Class<?> type = object.getClass();
        while (type != null) {
            try {
                Field field = type.getDeclaredField(name);
                field.setAccessible(true);
                return field.get(object);
            } catch (NoSuchFieldException e) {
                type = type.getSuperclass();
            } catch (Throwable e) {
                return null;
            }
        }
        return null;
    }

    private static Object invoke(Object object, String name, Class<?>[] parameterTypes, Object[] args) {
        if (object == null) {
            return null;
        }
        try {
            Method method = object.getClass().getMethod(name, parameterTypes);
            return method.invoke(object, args);
        } catch (Throwable e) {
            return null;
        }
    }

    private static boolean startPackageOnDisplay(String packageName, boolean forceStop) {
        if (packageName == null || packageName.length() == 0 || displayId == DISPLAY_NONE) {
            return false;
        }
        Object context = getFakeContext();
        if (context == null) {
            return false;
        }
        try {
            Object packageManager = context.getClass().getMethod("getPackageManager").invoke(context);
            Object intent = invoke(packageManager, "getLaunchIntentForPackage",
                    new Class<?>[]{String.class}, new Object[]{packageName});
            if (intent == null) {
                intent = invoke(packageManager, "getLeanbackLaunchIntentForPackage",
                        new Class<?>[]{String.class}, new Object[]{packageName});
            }
            if (intent == null) {
                System.err.println("NKAS_VD_START_NO_INTENT " + packageName);
                return false;
            }
            Class<?> intentClass = Class.forName("android.content.Intent");
            int newTask = intentClass.getField("FLAG_ACTIVITY_NEW_TASK").getInt(null);
            int exclude = intentClass.getField("FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS").getInt(null);
            intentClass.getMethod("addFlags", int.class).invoke(intent, newTask | exclude);

            Object activityManager = getActivityManagerProxy();
            if (forceStop && activityManager != null) {
                try {
                    activityManager.getClass().getMethod("forceStopPackage", String.class, int.class)
                            .invoke(activityManager, packageName, -2);
                } catch (Throwable ignored) {
                }
            }

            Class<?> optionsClass = Class.forName("android.app.ActivityOptions");
            Object options = optionsClass.getMethod("makeBasic").invoke(null);
            boolean displayOption = false;
            try {
                Method setDisplay = optionsClass.getDeclaredMethod("setLaunchDisplayId", int.class);
                setDisplay.setAccessible(true);
                setDisplay.invoke(options, displayId);
                displayOption = true;
            } catch (Throwable ignored) {
                try {
                    Field field = optionsClass.getDeclaredField("mLaunchDisplayId");
                    field.setAccessible(true);
                    field.setInt(options, displayId);
                    displayOption = true;
                } catch (Throwable ignoredAgain) {
                }
            }
            try {
                Method fullscreen = optionsClass.getDeclaredMethod("setLaunchWindowingMode", int.class);
                fullscreen.setAccessible(true);
                fullscreen.invoke(options, 1);
            } catch (Throwable ignored) {
            }
            Object bundle = optionsClass.getMethod("toBundle").invoke(options);
            if (activityManager != null && displayOption) {
                try {
                    Class<?> appThread = Class.forName("android.app.IApplicationThread");
                    Class<?> profilerInfo = Class.forName("android.app.ProfilerInfo");
                    Method start = activityManager.getClass().getMethod(
                            "startActivityAsUser", appThread, String.class, intentClass,
                            String.class, Class.forName("android.os.IBinder"), String.class,
                            int.class, int.class, profilerInfo, Class.forName("android.os.Bundle"),
                            int.class);
                    Object result = start.invoke(activityManager, null, "com.android.shell", intent,
                            null, null, null, 0, 0, null, bundle, -2);
                    if (!(result instanceof Number) || ((Number) result).intValue() >= 0) {
                        System.out.println("NKAS_VD_START_OK package=" + packageName
                                + " display=" + displayId);
                        return true;
                    }
                    System.err.println("NKAS_VD_START_RETURN " + result);
                } catch (Throwable e) {
                    System.err.println("NKAS_VD_START_REFLECTION " + e);
                }
            }
            return startViaAm(intent, packageName);
        } catch (Throwable e) {
            System.err.println("NKAS_VD_START_ERROR " + e);
            return false;
        }
    }

    private static boolean startViaAm(Object intent, String packageName) {
        String component = null;
        try {
            Object componentName = intent.getClass().getMethod("getComponent").invoke(intent);
            if (componentName != null) {
                component = (String) componentName.getClass()
                        .getMethod("flattenToShortString").invoke(componentName);
            }
        } catch (Throwable ignored) {
        }
        if (component == null || component.length() == 0) {
            return false;
        }
        try {
            String[] command = new String[]{"am", "start", "--display", String.valueOf(displayId),
                    "-n", component};
            Process process = Runtime.getRuntime().exec(command);
            int exitCode = process.waitFor();
            String stdout = readProcessStream(process.getInputStream());
            String stderr = readProcessStream(process.getErrorStream());
            if (stdout.length() > 0) {
                System.out.println("NKAS_VD_START_AM " + stdout.trim());
            }
            if (stderr.length() > 0) {
                System.err.println("NKAS_VD_START_AM_ERR " + stderr.trim());
            }
            return exitCode == 0 && stdout.indexOf("Error:") < 0 && stderr.indexOf("Error:") < 0;
        } catch (Throwable e) {
            System.err.println("NKAS_VD_START_AM_EXCEPTION " + e);
            return false;
        }
    }

    private static String readProcessStream(java.io.InputStream stream) {
        try {
            StringBuilder result = new StringBuilder();
            BufferedReader reader = new BufferedReader(new InputStreamReader(stream, "UTF-8"));
            String line;
            while ((line = reader.readLine()) != null) {
                if (result.length() > 0) {
                    result.append('\n');
                }
                result.append(line);
            }
            return result.toString();
        } catch (Throwable ignored) {
            return "";
        }
    }

    private static final class TaskInfo {
        private final int taskId;
        private final int displayId;

        private TaskInfo(int taskId, int displayId) {
            this.taskId = taskId;
            this.displayId = displayId;
        }
    }

    private static String componentPackage(Object component) {
        if (component == null) {
            return null;
        }
        try {
            Object packageName = component.getClass().getMethod("getPackageName").invoke(component);
            if (packageName != null) {
                return String.valueOf(packageName);
            }
        } catch (Throwable ignored) {
        }
        String text = String.valueOf(component);
        int slash = text.indexOf('/');
        return slash > 0 ? text.substring(0, slash) : null;
    }

    private static TaskInfo findTask(String packageName) {
        Object context = getFakeContext();
        if (context == null) {
            return null;
        }
        try {
            Object activityManager = context.getClass().getMethod(
                    "getSystemService", String.class).invoke(context, "activity");
            Object tasksObject = activityManager.getClass().getMethod("getRunningTasks", int.class)
                    .invoke(activityManager, 100);
            if (!(tasksObject instanceof java.util.List)) {
                return null;
            }
            for (Object task : (java.util.List<?>) tasksObject) {
                Object top = getField(task, "topActivity");
                Object base = getField(task, "baseActivity");
                if (!packageName.equals(componentPackage(top))
                        && !packageName.equals(componentPackage(base))) {
                    continue;
                }
                Number taskId = (Number) getField(task, "taskId");
                if (taskId == null) {
                    taskId = (Number) getField(task, "id");
                }
                Number taskDisplay = (Number) getField(task, "displayId");
                return new TaskInfo(taskId == null ? -1 : taskId.intValue(),
                        taskDisplay == null ? DISPLAY_NONE : taskDisplay.intValue());
            }
        } catch (Throwable e) {
            System.err.println("NKAS_VD_TASK_QUERY_ERROR " + e);
        }
        return null;
    }

    private static boolean moveTaskToDisplay(int taskId, int targetDisplayId) {
        if (taskId < 0) {
            return false;
        }
        Object manager = getActivityTaskManagerProxy();
        if (manager != null) {
            for (String methodName : new String[]{"moveRootTaskToDisplay", "moveStackToDisplay"}) {
                try {
                    Method method = manager.getClass().getMethod(methodName, int.class, int.class);
                    method.invoke(manager, taskId, targetDisplayId);
                    return true;
                } catch (Throwable ignored) {
                }
            }
        }
        try {
            Process process = Runtime.getRuntime().exec(new String[]{"am", "display", "move-stack",
                    String.valueOf(taskId), String.valueOf(targetDisplayId)});
            return process.waitFor() == 0;
        } catch (Throwable e) {
            System.err.println("NKAS_VD_MOVE_TASK_ERROR " + e);
            return false;
        }
    }

    private static boolean repinPackageOnDisplay(String packageName) {
        TaskInfo task = findTask(packageName);
        if (task != null && task.displayId == displayId) {
            return true;
        }
        if (task != null && moveTaskToDisplay(task.taskId, displayId)) {
            try {
                Thread.sleep(500L);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            TaskInfo moved = findTask(packageName);
            if (moved != null && moved.displayId == displayId) {
                return true;
            }
        }
        // Re-launching with ActivityOptions is the same fallback used by MAA-Meow
        // when a ROM moves a task back to the physical display.
        return startPackageOnDisplay(packageName, false);
    }

    private static final class FrameSnapshot {
        private final byte[] data;
        private final int width;
        private final int height;
        private final int bufferIndex;

        private FrameSnapshot(byte[] data, int width, int height, int bufferIndex) {
            this.data = data;
            this.width = width;
            this.height = height;
            this.bufferIndex = bufferIndex;
        }
    }

    private static void onImageAvailable(Object reader) {
        Object image = null;
        try {
            image = reader.getClass().getMethod("acquireLatestImage").invoke(reader);
            if (image == null) {
                return;
            }
            synchronized (FRAME_LOCK) {
                int bufferIndex = findWritableBuffer();
                if (bufferIndex == DISPLAY_NONE) {
                    // A slow client is still reading all buffers. Dropping this frame
                    // keeps ImageReader flowing and avoids the maxImages deadlock seen
                    // on Samsung/One UI devices.
                    return;
                }
                int[] size = copyImageRgbInto(image, bufferIndex);
                latestBuffer = bufferIndex;
                latestWidth = size[0];
                latestHeight = size[1];
                frameCount++;
            }
        } catch (Throwable e) {
            System.err.println("NKAS_VD_FRAME_ERROR " + e);
        } finally {
            // Never retain an Image past the callback. Some vendor ImageReader
            // implementations stop delivering frames when an image remains open.
            closeImage(image);
        }
    }

    private static int findWritableBuffer() {
        for (int i = 0; i < FRAME_BUFFER_COUNT; i++) {
            if (frameReaders[i] == 0) {
                return i;
            }
        }
        return DISPLAY_NONE;
    }

    private static int[] copyImageRgbInto(Object image, int bufferIndex) throws Exception {
        Class<?> imageClass = Class.forName("android.media.Image");
        int width = (Integer) imageClass.getMethod("getWidth").invoke(image);
        int height = (Integer) imageClass.getMethod("getHeight").invoke(image);
        if (width <= 0 || height <= 0 || width > 8192 || height > 8192) {
            throw new IllegalArgumentException("invalid image size " + width + "x" + height);
        }
        long byteCount = (long) width * height * 3L;
        if (byteCount > 64L * 1024L * 1024L) {
            throw new IllegalArgumentException("image is too large: " + width + "x" + height);
        }
        byte[] rgb = frameBuffers[bufferIndex];
        if (rgb == null || rgb.length != (int) byteCount) {
            rgb = new byte[(int) byteCount];
            frameBuffers[bufferIndex] = rgb;
        }
        Object[] planes = (Object[]) imageClass.getMethod("getPlanes").invoke(image);
        if (planes == null || planes.length == 0 || planes[0] == null) {
            throw new IllegalStateException("ImageReader returned no planes");
        }
        Class<?> planeClass = Class.forName("android.media.Image$Plane");
        Object plane = planes[0];
        ByteBuffer buffer = ((ByteBuffer) planeClass.getMethod("getBuffer").invoke(plane)).duplicate();
        int pixelStride = (Integer) planeClass.getMethod("getPixelStride").invoke(plane);
        int rowStride = (Integer) planeClass.getMethod("getRowStride").invoke(plane);
        int base = buffer.position();
        int limit = buffer.limit();
        if (pixelStride < 3 || rowStride < width * pixelStride) {
            throw new IllegalStateException("invalid image strides " + pixelStride + "/" + rowStride);
        }
        long lastByte = (long) base + (long) (height - 1) * rowStride
                + (long) (width - 1) * pixelStride + 3L;
        if (lastByte > limit) {
            throw new IllegalStateException("ImageReader plane is truncated: "
                    + lastByte + ">" + limit);
        }
        for (int y = 0; y < height; y++) {
            int row = base + y * rowStride;
            int dst = y * width * 3;
            for (int x = 0; x < width; x++) {
                int offset = row + x * pixelStride;
                // PixelFormat.RGBA_8888 is exposed as R,G,B,A by ImageReader.
                rgb[dst++] = buffer.get(offset);
                rgb[dst++] = buffer.get(offset + 1);
                rgb[dst++] = buffer.get(offset + 2);
            }
        }
        return new int[]{width, height};
    }

    private static FrameSnapshot acquireLatestFrame() {
        synchronized (FRAME_LOCK) {
            if (latestBuffer == DISPLAY_NONE || frameBuffers[latestBuffer] == null) {
                return null;
            }
            int index = latestBuffer;
            frameReaders[index]++;
            return new FrameSnapshot(frameBuffers[index], latestWidth, latestHeight, index);
        }
    }

    private static void releaseFrame(FrameSnapshot frame) {
        if (frame == null) {
            return;
        }
        synchronized (FRAME_LOCK) {
            if (frame.bufferIndex >= 0 && frame.bufferIndex < FRAME_BUFFER_COUNT
                    && frameReaders[frame.bufferIndex] > 0) {
                frameReaders[frame.bufferIndex]--;
            }
        }
    }

    private static void serve(String socketName) throws Exception {
        Class<?> serverClass = Class.forName("android.net.LocalServerSocket");
        Object server = serverClass.getConstructor(String.class).newInstance(socketName);
        while (running) {
            Object socket = serverClass.getMethod("accept").invoke(server);
            try {
                handleClient(socket);
            } finally {
                socket.getClass().getMethod("close").invoke(socket);
            }
        }
        serverClass.getMethod("close").invoke(server);
    }

    private static void handleClient(Object socket) throws Exception {
        BufferedReader reader = new BufferedReader(new InputStreamReader(
                (java.io.InputStream) socket.getClass().getMethod("getInputStream").invoke(socket),
                "UTF-8"));
        OutputStream rawOutput = (OutputStream) socket.getClass().getMethod("getOutputStream").invoke(socket);
        String command = reader.readLine();
        if ("FRAME".equals(command)) {
            FrameSnapshot frame = acquireLatestFrame();
            DataOutputStream output = new DataOutputStream(rawOutput);
            try {
                int width = frame == null ? 0 : frame.width;
                int height = frame == null ? 0 : frame.height;
                int size = frame == null ? 0 : frame.data.length;
                output.writeInt(width);
                output.writeInt(height);
                output.writeInt(size);
                if (frame != null) {
                    output.write(frame.data);
                }
                output.flush();
            } finally {
                releaseFrame(frame);
            }
        } else if ("INFO".equals(command)) {
            int width;
            int height;
            long frames;
            synchronized (FRAME_LOCK) {
                width = latestWidth > 0 ? latestWidth : actualDisplayWidth;
                height = latestHeight > 0 ? latestHeight : actualDisplayHeight;
                frames = frameCount;
            }
            rawOutput.write(("OK id=" + displayId + " frames=" + frames
                    + " size=" + width + "x" + height
                    + " rotation=" + displayRotation + "\n").getBytes("UTF-8"));
            rawOutput.flush();
        } else if (command != null && command.startsWith("START ")) {
            boolean started = startPackageOnDisplay(command.substring(6).trim(), false);
            rawOutput.write((started ? "OK\n" : "ERROR start\n").getBytes("UTF-8"));
            rawOutput.flush();
        } else if (command != null && command.startsWith("REPIN ")) {
            boolean repinned = repinPackageOnDisplay(command.substring(6).trim());
            rawOutput.write((repinned ? "OK\n" : "ERROR repin\n").getBytes("UTF-8"));
            rawOutput.flush();
        } else if ("STOP".equals(command)) {
            running = false;
            rawOutput.write("OK\n".getBytes("UTF-8"));
            rawOutput.flush();
        } else {
            rawOutput.write("ERROR\n".getBytes("UTF-8"));
            rawOutput.flush();
        }
    }

    private static void closeImage(Object image) {
        if (image == null) {
            return;
        }
        try {
            image.getClass().getMethod("close").invoke(image);
        } catch (Throwable ignored) {
        }
    }

    private static synchronized void release() {
        running = false;
        synchronized (FRAME_LOCK) {
            latestBuffer = DISPLAY_NONE;
            latestWidth = 0;
            latestHeight = 0;
            for (int i = 0; i < FRAME_BUFFER_COUNT; i++) {
                frameBuffers[i] = null;
                frameReaders[i] = 0;
            }
        }
        clearForcedDisplaySize();
        try {
            if (virtualDisplay != null) {
                virtualDisplay.getClass().getMethod("release").invoke(virtualDisplay);
            }
        } catch (Throwable ignored) {
        }
        try {
            if (imageReader != null) {
                imageReader.getClass().getMethod("close").invoke(imageReader);
            }
        } catch (Throwable ignored) {
        }
        virtualDisplay = null;
        imageReader = null;
        try {
            if (captureThread != null) {
                captureThread.getClass().getMethod("quitSafely").invoke(captureThread);
            }
        } catch (Throwable ignored) {
        }
        captureThread = null;
        displayId = DISPLAY_NONE;
    }
}
