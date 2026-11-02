server {
  server_name seandesmet.com www.seandesmet.com;
  root /var/www/seandesmet.com;
  include /etc/nginx/snippets/seandesmet-security-headers.conf;
  index index.html;
  location / { try_files $uri $uri/ =404; }

  listen 443 ssl;
  ssl_certificate     /etc/letsencrypt/live/seandesmet.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/seandesmet.com/privkey.pem;
  include             /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;
}

server {
  if ($host = seandesmet.com) { return 301 https://$host$request_uri; }
  listen 80;
  server_name seandesmet.com www.seandesmet.com;
  return 404;
}
