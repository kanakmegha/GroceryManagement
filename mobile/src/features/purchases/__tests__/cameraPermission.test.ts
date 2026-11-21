import {
  CameraPermissionApi,
  checkCameraPermissionStatus,
  resolveCameraPermissionForCapture,
} from '../cameraPermission';

describe('Expo camera permission handling for Android / Expo Go', () => {
  it('returns granted immediately when permission is already granted without re-requesting', async () => {
    const api: CameraPermissionApi = {
      getCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: true, canAskAgain: true }),
      requestCameraPermissionsAsync: jest.fn(),
    };

    const outcome = await resolveCameraPermissionForCapture(api);
    expect(outcome).toBe('granted');
    expect(api.requestCameraPermissionsAsync).not.toHaveBeenCalled();
  });

  it('requests permission when denied with canAskAgain=true and succeeds when user grants', async () => {
    const api: CameraPermissionApi = {
      getCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: false, canAskAgain: true }),
      requestCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: true, canAskAgain: true }),
    };

    const outcome = await resolveCameraPermissionForCapture(api);
    expect(outcome).toBe('granted');
    expect(api.requestCameraPermissionsAsync).toHaveBeenCalledTimes(1);
  });

  it('returns denied when user denies once and canAskAgain remains true', async () => {
    const api: CameraPermissionApi = {
      getCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: false, canAskAgain: true }),
      requestCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: false, canAskAgain: true }),
    };

    const outcome = await resolveCameraPermissionForCapture(api);
    expect(outcome).toBe('denied');
    expect(api.requestCameraPermissionsAsync).toHaveBeenCalledTimes(1);
  });

  it('transitions to permanently_denied when requestCameraPermissionsAsync returns canAskAgain=false', async () => {
    const api: CameraPermissionApi = {
      getCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: false, canAskAgain: true }),
      requestCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: false, canAskAgain: false }),
    };

    const outcome = await resolveCameraPermissionForCapture(api);
    expect(outcome).toBe('permanently_denied');
  });

  it('does NOT call requestCameraPermissionsAsync when OS permission is false and canAskAgain=false', async () => {
    const api: CameraPermissionApi = {
      getCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: false, canAskAgain: false }),
      requestCameraPermissionsAsync: jest.fn(),
      checkAndroidOsCameraPermission: jest.fn().mockResolvedValue(false),
    };

    const outcome = await resolveCameraPermissionForCapture(api);
    expect(outcome).toBe('permanently_denied');
    expect(api.requestCameraPermissionsAsync).not.toHaveBeenCalled();
  });

  it('handles Android OS granted in Settings when Expo Go scoped cache still reports canAskAgain=false', async () => {
    const api: CameraPermissionApi = {
      getCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: false, canAskAgain: false }),
      requestCameraPermissionsAsync: jest.fn().mockResolvedValue({ granted: false, canAskAgain: false }),
      checkAndroidOsCameraPermission: jest.fn().mockResolvedValue(true),
    };

    const outcome = await resolveCameraPermissionForCapture(api);
    expect(api.requestCameraPermissionsAsync).toHaveBeenCalledTimes(1);
    expect(outcome).toBe('expo_go_scoped_denied');
  });

  it('rechecks permission passively on return from Android Settings without triggering a dialog', async () => {
    const getPerm = jest
      .fn()
      .mockResolvedValueOnce({ granted: false, canAskAgain: false })
      .mockResolvedValueOnce({ granted: true, canAskAgain: true });

    expect(
      await checkCameraPermissionStatus({
        getCameraPermissionsAsync: getPerm,
        checkAndroidOsCameraPermission: jest.fn().mockResolvedValue(false),
      })
    ).toBe('permanently_denied');
    expect(
      await checkCameraPermissionStatus({
        getCameraPermissionsAsync: getPerm,
        checkAndroidOsCameraPermission: jest.fn().mockResolvedValue(true),
      })
    ).toBe('granted');
  });
});
