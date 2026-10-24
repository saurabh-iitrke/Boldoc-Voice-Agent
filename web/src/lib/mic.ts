/** The microphone settings for a browser call, asked for by every capture of the mic on the page (the call, the
 * permission check, the voice meter), so a browser never opens it without its echo cancellation: BolDoc's voice
 * from the speakers must not come back as the caller's words. Browsers default these to on; asking makes it certain,
 * and the same settings everywhere keep one capture from changing the processing of another. */
export const MIC: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
