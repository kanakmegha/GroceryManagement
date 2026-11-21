export type CameraPermissionOutcome =
  | 'granted'
  | 'denied'
  | 'permanently_denied'
  | 'expo_go_scoped_denied';

export interface CameraPermissionResponse {
  granted: boolean;
  canAskAgain?: boolean;
}

export interface CameraPermissionApi {
  getCameraPermissionsAsync: () => Promise<CameraPermissionResponse>;
  requestCameraPermissionsAsync: () => Promise<CameraPermissionResponse>;
  /** Optional Android OS-level check (PermissionsAndroid.check(CAMERA)). */
  checkAndroidOsCameraPermission?: () => Promise<boolean>;
}

/** Read-only check used on mount and when returning from Android Settings. */
export async function checkCameraPermissionStatus(
  api: Pick<
    CameraPermissionApi,
    'getCameraPermissionsAsync' | 'checkAndroidOsCameraPermission'
  >
): Promise<CameraPermissionOutcome> {
  const current = await api.getCameraPermissionsAsync();
  if (current.granted) {
    return 'granted';
  }
  if (api.checkAndroidOsCameraPermission) {
    const osGranted = await api.checkAndroidOsCameraPermission();
    if (osGranted) {
      return 'expo_go_scoped_denied';
    }
  }
  return current.canAskAgain === false ? 'permanently_denied' : 'denied';
}

/**
 * Check current camera permission and request only when Android permits asking again.
 * If Android OS permission is already granted (e.g. enabled in Settings) but Expo Go's
 * scoped permission cache is stale, still attempt requestCameraPermissionsAsync() and
 * surface 'expo_go_scoped_denied' instead of looping the user back to Settings.
 */
export async function resolveCameraPermissionForCapture(
  api: CameraPermissionApi
): Promise<CameraPermissionOutcome> {
  const current = await api.getCameraPermissionsAsync();
  if (current.granted) {
    return 'granted';
  }

  const osGranted = api.checkAndroidOsCameraPermission
    ? await api.checkAndroidOsCameraPermission()
    : false;

  if (current.canAskAgain === false && !osGranted) {
    return 'permanently_denied';
  }

  const requested = await api.requestCameraPermissionsAsync();
  if (requested.granted) {
    return 'granted';
  }

  if (osGranted) {
    return 'expo_go_scoped_denied';
  }
  return requested.canAskAgain === false ? 'permanently_denied' : 'denied';
}
