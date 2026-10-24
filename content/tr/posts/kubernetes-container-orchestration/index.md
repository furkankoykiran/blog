---
title: "Kubernetes ile Container Orkestrasyonu: Üretim Rehberi"
description: "Kubernetes'te üretime çıkmadan önce bilmeniz gerekenler: kaynak limitleri ve QoS, probe'lar, CrashLoopBackOff, Gateway API, Secret'lar, HPA ve güvenlik."
date: 2025-07-08T15:20:00+03:00
slug: "kubernetes-container-orkestrasyon"
postType: guide
tags: ["kubernetes", "docker", "devops", "security"]
categories: ["DevOps"]
coverAlt: "Kubernetes cluster'ında node'lara dağılmış pod'ları ve önlerindeki Gateway'i gösteren şema"
draft: false
---

Kubernetes ile container orkestrasyonu öğrenmeye başlayınca ilk hafta her şey yolunda gider: bir Deployment yazarsınız, pod'lar ayağa kalkar. Sorunlar üretimde çıkar. Pod'lar sebepsiz yere `OOMKilled` olur, bir veritabanı yavaşlaması bütün servisi `CrashLoopBackOff`'a sokar, deploy sırasında birkaç istek 502 döner.

Bu yazı, tutorial'ların atladığı ama üretimde karşıma çıkan konuların bir derlemesi. Tek bir Python API'sinin Deployment'ından başlayıp kaynak yönetimi, probe'lar, hata ayıklama, ağ, Secret'lar ve ölçekleme üzerinden gidiyorum. Örnekler Kubernetes 1.3x sürümleri için yazıldı.

## Önce şunu sorun: Kubernetes gerekli mi?

Tek bir sunucuda üç servis çalıştırıyorsanız muhtemelen gerekli değil. Docker Compose ve önünde bir [Traefik](/posts/traefik-reverse-proxy-ssl-yonetimi/) veya konteynersiz bir kurulumda systemd çok daha az iş çıkarır. Kubernetes; birden fazla makineye yayılmanız, otomatik ölçeklemeye, sıfır kesintili rolling update'e ve ekipler arası standart bir dağıtım arayüzüne ihtiyacınız olduğunda kendini amorti ediyor. Yönetilen bir cluster (EKS, GKE, AKS ve benzerleri) kullanmıyorsanız kontrol düzleminin bakımı da sizde, bunu hesaba katın.

## Üretime hazır bir Deployment

Önce yazının geri kalanında parça parça açacağım tam manifest:

```yaml {title="api-deployment.yaml"}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  labels:
    app: api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api
  template:
    metadata:
      labels:
        app: api
    spec:
      terminationGracePeriodSeconds: 45
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              app: api
      containers:
        - name: api
          image: ghcr.io/example/api:sha-1a2b3c4
          ports:
            - name: http
              containerPort: 8000
          envFrom:
            - secretRef:
                name: api-secrets
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              memory: 256Mi
          startupProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 2
            failureThreshold: 30
          readinessProbe:
            httpGet:
              path: /ready
              port: http
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 10
            failureThreshold: 3
          lifecycle:
            preStop:
              sleep:
                seconds: 10
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
```

İmajın kendisini nasıl hazırladığımı [Docker ile Python uygulaması yazımda](/posts/docker-ile-python-uygulamasi-containerization/) anlattım. Oradaki imaj root olmayan bir kullanıcıyla çalıştığı için `runAsNonRoot: true` sorunsuz geçiyor.

## Kaynaklar ve QoS sınıfları

`requests`, scheduler'ın pod'u yerleştirirken ayırdığı kaynak. `limits`, container'ın aşamayacağı tavan. İkisinin ilişkisi pod'un QoS sınıfını belirler:

- Guaranteed: Her container'da CPU ve bellek için request ve limit eşit. Node'da bellek sıkıştığında en son bunlar tahliye edilir.
- Burstable: En az bir request var ama Guaranteed şartları sağlanmıyor. Çoğu web servisi burada.
- BestEffort: Hiç request veya limit yok. Node sıkışınca ilk bunlar gider. Üretimde kullanmıyorum.

Bellek ve CPU limitleri farklı davranır. Container bellek limitini aşarsa çekirdek onu öldürür ve `OOMKilled` görürsünüz. CPU limitini aşarsa öldürülmez, yavaşlatılır (throttling). Bu yüzden benim varsayılanım şu: bellek limiti request'e eşit, CPU için yalnızca request, CPU limiti yok. Bellek limiti, bir sızıntının node'daki diğer pod'ları etkilemesini engelliyor. CPU limiti koymamak, boşta CPU varken servisin yapay olarak yavaşlamasını engelliyor. Bu tartışmalı bir tercih. Çok kiracılı cluster'larda veya maliyet paylaşımı yapıyorsanız CPU limiti de koymanız gerekebilir.

Request değerlerini tahminle değil ölçerek belirleyin. `kubectl top pods` birkaç gün boyunca gerçek kullanımı gösterir. Kubernetes 1.35 ile GA olan in-place pod resize sayesinde, çalışan pod'ların kaynaklarını pod'u yeniden başlatmadan değiştirmek de mümkün.

## Probe'lar: startup, readiness, liveness

Bu üçünü karıştırmak zincirleme kazalara sebep oluyor:

- startupProbe uygulama ilk kez ayağa kalkarken çalışır. Başarılı olana kadar diğer iki probe devreye girmez. Yavaş açılan uygulamalarda liveness'ın daha açılış bitmeden pod'u öldürmesini engeller.
- readinessProbe "trafik alabilir misin?" sorusunu sorar. Başarısız olursa pod Service endpoint'lerinden çıkarılır ama yeniden başlatılmaz.
- livenessProbe "kilitlendin mi?" sorusunu sorar. Başarısız olursa container yeniden başlatılır.

En sık gördüğüm hata, liveness probe'unu veritabanına bağlamak. Veritabanı yavaşladığında bütün pod'ların liveness kontrolü başarısız olur, hepsi yeniden başlar, açılırken veritabanına daha fazla bağlantı açarlar ve durum kötüleşir. Benim kuralım: `/health` yalnızca sürecin cevap verip vermediğine bakar, dış bağımlılıkları kontrol etmez. Veritabanı kontrolü `/ready` endpoint'inde durur. Veritabanı yoksa pod trafik almaz ama yeniden başlatılmaz da.

`preStop` içindeki 10 saniyelik bekleme ve `terminationGracePeriodSeconds` ilişkisini, sinyal yönetimiyle birlikte [zarif kapanış yazımda](/posts/zarif-kapanis-sinyal-isleme/) ayrıntılı anlattım.

## CrashLoopBackOff'ta ne yapıyorum

Pod'lar sürekli yeniden başlıyorsa sırayla şunlara bakıyorum:

```bash
kubectl get pods -l app=api                 # RESTARTS sütunu
kubectl logs deploy/api --previous          # ölen container'ın son logları
kubectl describe pod <pod-adı>              # Last State, Reason, Events
kubectl get events --sort-by=.lastTimestamp # cluster seviyesinde olaylar
```

`--previous` en çok işe yarayan bayrak. Normal `kubectl logs` yeni başlamış container'ın loglarını gösterir, orada genelde sadece açılış mesajı vardır. Asıl stack trace, ölen önceki container'dadır. `describe` çıktısındaki `Reason` alanı `OOMKilled`, `Error` veya `ContainerCannotRun` gibi altyapı kaynaklı sebepleri gösterir. `ImagePullBackOff` ise genelde yanlış etiket veya eksik registry yetkisi demek.

Container'da shell olmayan (distroless) imajlarda içeri girmek için geçici bir debug container ekleyebilirsiniz:

```bash
kubectl debug -it <pod-adı> --image=busybox:1.37 --target=api
```

## Servisler ve dışarıya açılmak: Ingress yerine Gateway API

Service tipleri basit:

- ClusterIP: Yalnızca cluster içinden erişilir. Servisler arası iletişim için varsayılan.
- NodePort: Her node'da bir port açar. Üretimde nadiren doğrudan kullanıyorum.
- LoadBalancer: Bulut sağlayıcısından bir yük dengeleyici ister.

HTTP trafiğini dışarıya açmak için yıllarca Ingress kaynağı ve ingress-nginx controller'ı kullanıldı. Kubernetes projesi [ingress-nginx'i emekliye ayırdı](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/): bakımı sona erdi ve repo arşivlendi. Mevcut kurulumlar çalışmaya devam ediyor ama artık güvenlik yaması çıkmıyor.

![GitHub'da kubernetes/ingress-nginx reposu: sarı arşiv uyarısı, depo artık salt okunur ve Public archive etiketi taşıyor](img/ingress-nginx-archived.webp "ingress-nginx reposu arşivlendi. Kaynak: GitHub, kubernetes/ingress-nginx")

Yeni bir cluster'da Gateway API kullanıyorum. Envoy Gateway, Cilium, Istio, NGINX Gateway Fabric ve Traefik gibi birçok uygulaması var.

```yaml {title="api-route.yaml"}
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  selector:
    app: api
  ports:
    - port: 80
      targetPort: http
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: api
spec:
  parentRefs:
    - name: public-gateway
      namespace: gateway-system
  hostnames:
    - api.example.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: api
          port: 80
```

Bu iki kaynakla bir isteğin yolu şöyle: Gateway isteği karşılıyor, HTTPRoute onu `api` Service'ine veriyor, Service de farklı node'lardaki hazır pod'lara dağıtıyor.

Gateway API'nin bana en faydalı yanı rol ayrımı. `Gateway` kaynağını (dinlenen portlar, TLS sertifikaları) cluster yöneticisi tanımlıyor, uygulama ekibi yalnızca kendi `HTTPRoute`'unu yazıyor. Ingress'te controller'a özel annotation'larla yapılan ağırlıklı yönlendirme, header eşleştirme gibi işler burada standart alanlar.

## ConfigMap ve Secret

Secret'lar varsayılan olarak yalnızca base64 ile kodlanır, şifrelenmez. `kubectl get secret -o yaml` yetkisi olan herkes içeriği okuyabilir. Yapılacaklar:

- etcd'de şifreleme (encryption at rest) açık mı kontrol edin. Yönetilen cluster'larda genelde açıktır, kendi kurduğunuz cluster'da siz açarsınız.
- Secret okuma yetkisini RBAC ile daraltın.
- Secret'ları Git'e düz metin olarak koymayın. İki yaygın yol var: Sealed Secrets ile yalnızca cluster'ın açabileceği şifreli bir nesneyi Git'te saklamak ya da External Secrets Operator ile değerleri AWS Secrets Manager, Vault gibi bir kasadan çekmek. Kasa zaten varsa ikincisini tercih ediyorum.

## Ölçekleme: HPA ve PodDisruptionBudget

Horizontal Pod Autoscaler, metriklere göre replica sayısını değiştirir. Çalışması için cluster'da metrics-server kurulu olmalı. `kubectl top pods` hata veriyorsa HPA da çalışmaz.

```yaml {title="api-hpa.yaml"}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api
  minReplicas: 3
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: api
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: api
```

`averageUtilization: 70`, CPU request değerinin yüzde 70'i demek. Request'i yanlış belirlediyseniz HPA da yanlış karar verir. HPA kullanırken Deployment'taki `replicas` alanını GitOps aracınızın sürekli geri yazmadığından emin olun, yoksa ikisi birbiriyle kavga eder.

PodDisruptionBudget, node bakımı gibi gönüllü kesintilerde aynı anda kaç pod'un gidebileceğini sınırlar. `kubectl drain <node> --ignore-daemonsets` çalıştırdığınızda Kubernetes bu bütçeye uyarak pod'ları tahliye eder. Bakım bitince `kubectl uncordon <node>` ile node tekrar pod almaya başlar.

## Durumlu servisler ve depolama

Deployment'taki pod'lar birbirinin aynısıdır, isimleri rastgeledir ve herhangi biri silinip yeniden oluşabilir. StatefulSet'te pod'ların sabit isimleri (`db-0`, `db-1`) ve her birinin kendi kalıcı diski vardır. Sırayla açılır, sırayla kapanırlar. Veritabanı, Kafka veya Redis gibi durumlu sistemler için StatefulSet kullanılır.

Depolamada dikkat edilecek nokta erişim modu. Bulut blok diskleri (EBS, Persistent Disk) genelde `ReadWriteOnce`: aynı anda tek bir node bağlayabilir. Birden fazla pod'un farklı node'lardan aynı diske yazması gerekiyorsa `ReadWriteMany` destekleyen bir depolama (NFS, EFS gibi) gerekir. Açıkçası, üretimde veritabanını Kubernetes'e taşımadan önce yönetilen bir veritabanı servisini düşünürüm. Durumlu sistemleri cluster'da işletmek, durumsuz bir API'yi işletmekten çok daha fazla emek istiyor.

## Ağ politikaları ve RBAC

Varsayılan olarak her pod her pod'la konuşabilir. Bir web pod'unu ele geçiren saldırgan veritabanına doğrudan bağlanabilir. NetworkPolicy ile önce namespace'teki tüm girişi kapatıp sonra ihtiyaç olanı açıyorum:

```yaml {title="network-policies.yaml"}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
spec:
  podSelector: {}
  policyTypes:
    - Ingress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: db-from-api
spec:
  podSelector:
    matchLabels:
      app: database
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: api
      ports:
        - port: 5432
```

NetworkPolicy'yi uygulayan şey CNI eklentisi. Cilium veya Calico bu kuralları uygular. Bazı basit CNI'lar kuralları kabul eder ama uygulamaz, bu yüzden kuralı yazdıktan sonra gerçekten trafiği engellediğini test edin.

RBAC'ta kuralım basit: kimseye `cluster-admin` vermemek. Geliştiricilere kendi namespace'lerinde bir `Role` ve `RoleBinding`, CI'a yalnızca deploy ettiği namespace'te gereken yetkiler.

## Helm, GitOps ve service mesh

Helm, paketlenmiş uygulamaları kurmak için iyi. Kendi servislerim için ise yüzlerce satırlık, her alanı `if` ile şartlanmış chart'lar yazmaktan kaçınıyorum. Çoğu zaman Kustomize ile düz manifest'ler daha okunur. Değişiklikleri CI'dan `kubectl apply` ile değil, Argo CD veya Flux gibi bir GitOps aracıyla Git'ten uyguluyorum. "Cluster'da çalışan şey ne?" sorusunun cevabı her zaman Git'te oluyor. CI tarafını [GitHub Actions CI/CD yazımda](/posts/github-actions-cicd-pipeline/) anlattım.

Service mesh (Istio, Linkerd) mTLS, ayrıntılı trafik yönetimi ve servisler arası gözlemlenebilirlik getiriyor ama karşılığında karmaşıklık ve kaynak maliyeti de getiriyor. Onlarca servisiniz ve bunlara somut bir ihtiyacınız yoksa ben kurmazdım.

## Üretime çıkmadan kontrol listesi

- Her container'da bellek request ve limiti var mı?
- startup, readiness ve liveness probe'ları ayrı ve liveness dış bağımlılıklara bakmıyor mu?
- `preStop` beklemesi ve yeterli `terminationGracePeriodSeconds` var mı?
- Pod'lar root olmayan kullanıcıyla, `allowPrivilegeEscalation: false` ile mi çalışıyor?
- Pod'lar node'lara yayılıyor mu (`topologySpreadConstraints`)?
- PDB ve HPA tanımlı mı?
- Namespace'te default-deny NetworkPolicy var mı ve CNI bunu uyguluyor mu?
- Secret'lar Git'te düz metin olarak durmuyor mu?

## Sırada ne var

Buradaki manifest'leri yerelde denemek için kind veya k3d ile birkaç dakikada bir cluster kurabilirsiniz. İlk deneme olarak bir pod'u `kubectl delete pod` ile silin ve readiness ile `preStop` beklemesi sayesinde isteklerin kesilmediğini gözleyin. İmajı CI'da derleyip etiketlemek için [GitHub Actions CI/CD yazıma](/posts/github-actions-cicd-pipeline/), kapanış tarafı için [zarif kapanış yazıma](/posts/zarif-kapanis-sinyal-isleme/) bakabilirsiniz. Her alanın tam açıklaması [Kubernetes dokümantasyonunda](https://kubernetes.io/docs/home/), Gateway API için de [resmi sitesinde](https://gateway-api.sigs.k8s.io/).
