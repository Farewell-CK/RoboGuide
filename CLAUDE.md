# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

GuideAssistant (`TempAndroid`) is a Kotlin Android app template/starter project (package
`com.seaway.guideassistant`). It is not a specific product — it exists to demonstrate a preconfigured
stack (networking, DI, view binding, navigation, etc.) that real apps are meant to be built on top of.
The `prototype/` directory at the repo root is an unrelated static HTML/JS/CSS mockup, not part of the
Android build.

Code and comments in this repo are predominantly Chinese; match that convention when editing existing
files (comments, README, string resources) unless the user asks otherwise.

## Modules

- `:app` — the application module, package `com.seaway.guideassistant`.
- `:smallutils` — a library module of standalone Android utility classes/views, package
  `com.seaway.smallutils`. `:app` depends on `:smallutils` (`implementation project(path: ':smallutils')`).
  Utilities here should stay generic/reusable; app-specific logic belongs in `:app`.

## Build / run commands

Standard Gradle Android project (Gradle 8.0.2 plugin, Kotlin 1.9.24, compileSdk 33, minSdk 21,
targetSdk 31, Java 8 target).

```bash
./gradlew assembleDebug          # build debug APK
./gradlew assembleRelease        # build release APK (signed with smallcake.jks, see below)
./gradlew installDebug           # build + install debug APK on connected device/emulator
./gradlew clean
./gradlew :smallutils:assembleDebug   # build a single module
./gradlew test                   # unit tests (JVM) across all modules
./gradlew connectedAndroidTest   # instrumented tests on device/emulator
./gradlew :app:testDebugUnitTest --tests "com.seaway.guideassistant.SomeTest"  # single test class
```

Debug and release build types both use `signingConfigs.release` (backed by the committed
`smallcake.jks` keystore, alias `xiao`, password `888888` — this is a template placeholder, not a
real secret) so both variants are always signed. `minifyEnabled` is `false` for both.

APK output filenames are generated dynamically as
`<last segment of applicationId>_v<versionName>_<timestamp>_<debug|release>.apk` (see
`app/build.gradle`'s `releaseTime()` / `outputFileName` logic).

There are third-party Maven repos (Aliyun mirrors, JitPack) declared in the root `build.gradle`;
network access to these (and `jcenter()`, which is EOL) is required for a clean dependency resolve.

## Architecture

### Dependency injection (Koin)

- `MyApplication.onCreate()` calls `startKoin { modules(httpModule) }`. `httpModule`
  (`module/HttpModule.kt`) is the single Koin module and wires up: Gson, the shared `OkHttpClient`
  (with logging/error interceptor `HandleErrorInterceptor` and a common-headers interceptor), one or
  more named `Retrofit` instances (default + `named("hasUrl")` / `named("siteUrl")` variants for
  hitting alternate base URLs via `parametersOf(url)`), API interface implementations, and
  `DataProvider`.
- **Adding a new network API category** (the documented pattern, see README §6): define a Retrofit
  `XxxApi` interface, an `XxxImpl : XxxApi, KoinComponent` that injects the interface and applies the
  `.im()` scheduler extension, register both as Koin `single {}` in `HttpModule.kt`, then expose the
  impl as a property on `DataProvider` (`http/DataProvider.kt`). `BaseActivity`/`BaseBindFragment`
  already inject `DataProvider` as `dataProvider` via Koin's `get()`.
- Existing API categories: `api/WeatherApi.kt` + `WeatherImpl`, `api/MobileApi.kt` + `MobileImpl`, each
  hitting a different base URL declared in `base/Constant.kt`.

### Networking call chain

Calls are chained through Kotlin extension functions in `http/HttpKtx.kt`:

```kotlin
dataProvider.weather.query()
    .bindLife(provider)   // cancels the RxJava subscription on Activity/Fragment ON_DESTROY
    .sub({ bind.item = it.result })   // only handle the success payload
```

- `.im()` (used inside `*Impl` classes) subscribes on `Schedulers.io()` and observes on
  `AndroidSchedulers.mainThread()`.
- `.bindLife(provider)` binds the `Observable` to a `LifecycleProvider<Lifecycle.Event>` (RxLifecycle)
  so requests are cancelled automatically when the screen is destroyed.
- `.sub(success, fail?, dialog?, ref?)` subscribes via `OnDataSuccessListener`, unwrapping
  `BaseResponse<T>`; pass `dialog` to show/hide a loading indicator, `fail` to handle errors yourself
  (otherwise a default error toast/log happens), `ref` (a `SwipeRefreshLayout`) to stop its refresh
  spinner automatically.
- Responses are `BaseResponse<T>` (`http/BaseResponse.kt`); `ResponseBodyInterceptor` /
  `HandleErrorInterceptor` sit in the OkHttp chain for logging and error normalization.

### Base classes

- `BaseActivity` (`base/BaseActivity.kt`): injects `dataProvider` (Koin), creates a `LoadDialog`, an
  RxLifecycle `LifecycleProvider`, and binds Apollo (`event/notification bus`) for `@Receive`-annotated
  methods; registers itself with `ActivityCollector` (app-wide activity stack, in `:smallutils`).
  Provides `goActivity(cls)` / `goActivity(cls, id)` helpers.
- `BaseBindActivity<VB : ViewBinding>` (`base/BaseBindActivity.kt`): extends `BaseActivity`, inflates
  the generic `VB` view/data binding automatically (`bind.xxx` — no `findViewById`/`onCreateView`
  boilerplate), and installs a common `NavigationBar` (from `:smallutils`) before calling the
  subclass's `onCreate(savedInstanceState, bar)`. **Screens should override
  `onCreate(savedInstanceState, bar)`, not the base `onCreate(savedInstanceState)`.** The binding type
  is derived from the layout name (`activity_main.xml` → `ActivityMainBinding`).
- `BaseBindFragment<VB : ViewBinding>` (`base/BaseBindFragment.kt`): same view-binding-by-generics
  pattern for fragments; `bind` is only valid between `onCreateView` and `onDestroyView`. Also injects
  `dataProvider`, a `LifecycleProvider`, and Apollo binding, and pulls its `LoadDialog` from the host
  `BaseActivity`.
- Data binding (`dataBinding { enabled = true }`) is enabled alongside ViewBinding — layouts can use
  `<layout>` + `<data><variable>` and be bound with e.g. `bind.user = UserBean(...)`. See
  `DataBindingAdapter.kt` for custom XML attribute adapters.

### Cross-screen events (Apollo)

Screens communicate via the Apollo event bus (`com.github.lsxiao.Apollo`) rather than callbacks/EventBus:
emit with `Apollo.emit("event")`, receive with a method annotated `@Receive("event")` on any
Activity/Fragment that has been bound (base classes already call `Apollo.bind(this)` /
`mBinder?.unbind()`).

### Bottom navigation

`utils/BottomNavUtils.kt` (`BottomNavUtils.initTabNavi(activity, tabLayout, viewPager, tabs, fragments)`)
wires a `ViewPager2` to a custom tab bar (`TabNavBottomBean` list) — see `MainActivity` for the
canonical 3-tab (Home/List/Mine) setup.

### Storage / misc singletons

- MMKV (`utils/MMKVUtils.kt`) replaces `SharedPreferences`; initialized in `MyApplication`.
- `ActivityCollector` (`:smallutils`) tracks all live Activities app-wide.
- `ShapeCreator` / `ShapeView` and `DpUtils`/`DpPxUtils` avoid hand-written `shape_*.xml` drawables and
  manual dp/px conversion respectively.
- Screen-size adaptation is handled by the ScreenMatch plugin (`screenMatch.properties`,
  `screenMatch_example_dimens.xml`, generated `values-sw*dp/dimens.xml` buckets) rather than
  `ConstraintLayout` percentage tricks — regenerate those buckets with the ScreenMatch tool rather than
  hand-editing many `dimens.xml` copies.

## Conventions to follow when extending this template

- New network endpoints: follow the `Api` interface + `Impl : Api, KoinComponent` + Koin `single {}` +
  `DataProvider` property pattern above, don't call Retrofit/OkHttp directly from UI code.
- New screens: extend `BaseBindActivity<XxxBinding>` or `BaseBindFragment<XxxBinding>`, not
  `AppCompatActivity`/`Fragment` directly, to get view binding, DI, lifecycle-bound Rx, and Apollo for
  free.
- Prefer the existing `:smallutils` utility (see README's tool list) over adding a new third-party
  library for the same job — the module already wraps common needs (toast throttling, file ops, time
  formatting, clipboard, keyboard, shapes, spannables, etc.).
