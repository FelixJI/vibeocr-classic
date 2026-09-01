# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_window.ui'
##
## Created by: Qt User Interface Compiler version 6.11.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (
    QCoreApplication,
    QMetaObject,
    QSize,
    Qt,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.widgets.preview_widget import PreviewWidget


class Ui_MainWindowWidget(object):
    def setupUi(self, MainWindowWidget):
        if not MainWindowWidget.objectName():
            MainWindowWidget.setObjectName("MainWindowWidget")
        self.verticalLayout = QVBoxLayout(MainWindowWidget)
        self.verticalLayout.setSpacing(0)
        self.verticalLayout.setObjectName("verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.tabWidget = QTabWidget(MainWindowWidget)
        self.tabWidget.setObjectName("tabWidget")
        self.tabOCR = QWidget()
        self.tabOCR.setObjectName("tabOCR")
        self.verticalLayout_ocr = QVBoxLayout(self.tabOCR)
        self.verticalLayout_ocr.setSpacing(0)
        self.verticalLayout_ocr.setObjectName("verticalLayout_ocr")
        self.verticalLayout_ocr.setContentsMargins(9, 9, 9, 9)
        self.ocrSplitter = QSplitter(self.tabOCR)
        self.ocrSplitter.setObjectName("ocrSplitter")
        self.ocrSplitter.setOrientation(Qt.Horizontal)
        self.previewWidget = PreviewWidget(self.ocrSplitter)
        self.previewWidget.setObjectName("previewWidget")
        self.previewWidget.setMinimumSize(QSize(300, 0))
        self.ocrSplitter.addWidget(self.previewWidget)
        self.resultPanel = QWidget(self.ocrSplitter)
        self.resultPanel.setObjectName("resultPanel")
        self.resultPanel.setMinimumSize(QSize(300, 0))
        self.verticalLayout_2 = QVBoxLayout(self.resultPanel)
        self.verticalLayout_2.setSpacing(6)
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.headerLayout = QHBoxLayout()
        self.headerLayout.setObjectName("headerLayout")
        self.labelResultTitle = QLabel(self.resultPanel)
        self.labelResultTitle.setObjectName("labelResultTitle")

        self.headerLayout.addWidget(self.labelResultTitle)

        self.horizontalSpacer = QSpacerItem(
            40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        self.headerLayout.addItem(self.horizontalSpacer)

        self.verticalLayout_2.addLayout(self.headerLayout)

        self.copyButtonsLayout = QHBoxLayout()
        self.copyButtonsLayout.setSpacing(4)
        self.copyButtonsLayout.setObjectName("copyButtonsLayout")
        self.btnCopyRich = QPushButton(self.resultPanel)
        self.btnCopyRich.setObjectName("btnCopyRich")

        self.copyButtonsLayout.addWidget(self.btnCopyRich)

        self.btnCopyMarkdown = QPushButton(self.resultPanel)
        self.btnCopyMarkdown.setObjectName("btnCopyMarkdown")

        self.copyButtonsLayout.addWidget(self.btnCopyMarkdown)

        self.btnCopyPlain = QPushButton(self.resultPanel)
        self.btnCopyPlain.setObjectName("btnCopyPlain")

        self.copyButtonsLayout.addWidget(self.btnCopyPlain)

        self.verticalLayout_2.addLayout(self.copyButtonsLayout)

        self.ocrSplitter.addWidget(self.resultPanel)

        self.verticalLayout_ocr.addWidget(self.ocrSplitter)

        self.tabWidget.addTab(self.tabOCR, "")
        self.tabSettings = QWidget()
        self.tabSettings.setObjectName("tabSettings")
        self.settingsHLayout = QHBoxLayout(self.tabSettings)
        self.settingsHLayout.setSpacing(0)
        self.settingsHLayout.setObjectName("settingsHLayout")
        self.settingsHLayout.setContentsMargins(0, 0, 0, 0)
        self.settingsNavList = QListWidget(self.tabSettings)
        QListWidgetItem(self.settingsNavList)
        QListWidgetItem(self.settingsNavList)
        QListWidgetItem(self.settingsNavList)
        QListWidgetItem(self.settingsNavList)
        self.settingsNavList.setObjectName("settingsNavList")
        self.settingsNavList.setMinimumSize(QSize(150, 0))
        self.settingsNavList.setMaximumSize(QSize(180, 16777215))
        self.settingsNavList.setFrameShape(QFrame.NoFrame)
        self.settingsNavList.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settingsNavList.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.settingsHLayout.addWidget(self.settingsNavList)

        self.settingsStackedWidget = QStackedWidget(self.tabSettings)
        self.settingsStackedWidget.setObjectName("settingsStackedWidget")
        self.pageGeneral = QWidget()
        self.pageGeneral.setObjectName("pageGeneral")
        self.pageGeneralLayout = QVBoxLayout(self.pageGeneral)
        self.pageGeneralLayout.setSpacing(12)
        self.pageGeneralLayout.setObjectName("pageGeneralLayout")
        self.pageGeneralLayout.setContentsMargins(16, 16, 16, 16)
        self.groupAppSettings = QGroupBox(self.pageGeneral)
        self.groupAppSettings.setObjectName("groupAppSettings")
        self.appSettingsLayout = QVBoxLayout(self.groupAppSettings)
        self.appSettingsLayout.setSpacing(8)
        self.appSettingsLayout.setObjectName("appSettingsLayout")
        self.labelToolbarDesc = QLabel(self.groupAppSettings)
        self.labelToolbarDesc.setObjectName("labelToolbarDesc")
        self.labelToolbarDesc.setWordWrap(True)

        self.appSettingsLayout.addWidget(self.labelToolbarDesc)

        self.chkShowToolbar = QCheckBox(self.groupAppSettings)
        self.chkShowToolbar.setObjectName("chkShowToolbar")

        self.appSettingsLayout.addWidget(self.chkShowToolbar)

        self.toolbarSubOptions = QWidget(self.groupAppSettings)
        self.toolbarSubOptions.setObjectName("toolbarSubOptions")
        self.toolbarSubLayout = QVBoxLayout(self.toolbarSubOptions)
        self.toolbarSubLayout.setSpacing(8)
        self.toolbarSubLayout.setObjectName("toolbarSubLayout")
        self.toolbarSubLayout.setContentsMargins(20, 0, 0, 0)
        self.chkAutoHideToolbar = QCheckBox(self.toolbarSubOptions)
        self.chkAutoHideToolbar.setObjectName("chkAutoHideToolbar")

        self.toolbarSubLayout.addWidget(self.chkAutoHideToolbar)

        self.toolbarFieldsForm = QFormLayout()
        self.toolbarFieldsForm.setObjectName("toolbarFieldsForm")
        self.toolbarFieldsForm.setLabelAlignment(
            Qt.AlignRight | Qt.AlignTrailing | Qt.AlignVCenter
        )
        self.labelHideDelay = QLabel(self.toolbarSubOptions)
        self.labelHideDelay.setObjectName("labelHideDelay")

        self.toolbarFieldsForm.setWidget(
            0, QFormLayout.ItemRole.LabelRole, self.labelHideDelay
        )

        self.spinHideDelay = QSpinBox(self.toolbarSubOptions)
        self.spinHideDelay.setObjectName("spinHideDelay")
        self.spinHideDelay.setMinimum(100)
        self.spinHideDelay.setMaximum(5000)
        self.spinHideDelay.setSingleStep(100)
        self.spinHideDelay.setValue(500)

        self.toolbarFieldsForm.setWidget(
            0, QFormLayout.ItemRole.FieldRole, self.spinHideDelay
        )

        self.labelPeekPixels = QLabel(self.toolbarSubOptions)
        self.labelPeekPixels.setObjectName("labelPeekPixels")

        self.toolbarFieldsForm.setWidget(
            1, QFormLayout.ItemRole.LabelRole, self.labelPeekPixels
        )

        self.spinPeekPixels = QSpinBox(self.toolbarSubOptions)
        self.spinPeekPixels.setObjectName("spinPeekPixels")
        self.spinPeekPixels.setMinimum(1)
        self.spinPeekPixels.setMaximum(20)
        self.spinPeekPixels.setSingleStep(1)
        self.spinPeekPixels.setValue(3)

        self.toolbarFieldsForm.setWidget(
            1, QFormLayout.ItemRole.FieldRole, self.spinPeekPixels
        )

        self.toolbarSubLayout.addLayout(self.toolbarFieldsForm)

        self.appSettingsLayout.addWidget(self.toolbarSubOptions)

        self.chkMinimizeToTray = QCheckBox(self.groupAppSettings)
        self.chkMinimizeToTray.setObjectName("chkMinimizeToTray")

        self.appSettingsLayout.addWidget(self.chkMinimizeToTray)

        self.chkAutoStart = QCheckBox(self.groupAppSettings)
        self.chkAutoStart.setObjectName("chkAutoStart")

        self.appSettingsLayout.addWidget(self.chkAutoStart)

        self.pageGeneralLayout.addWidget(self.groupAppSettings)

        self.spacerGeneralPage = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )

        self.pageGeneralLayout.addItem(self.spacerGeneralPage)

        self.settingsStackedWidget.addWidget(self.pageGeneral)
        self.pageRecognition = QWidget()
        self.pageRecognition.setObjectName("pageRecognition")
        self.pageRecognitionLayout = QVBoxLayout(self.pageRecognition)
        self.pageRecognitionLayout.setSpacing(12)
        self.pageRecognitionLayout.setObjectName("pageRecognitionLayout")
        self.pageRecognitionLayout.setContentsMargins(16, 16, 16, 16)
        self.groupEngineAvailability = QGroupBox(self.pageRecognition)
        self.groupEngineAvailability.setObjectName("groupEngineAvailability")
        self.engineAvailabilityLayout = QVBoxLayout(self.groupEngineAvailability)
        self.engineAvailabilityLayout.setSpacing(6)
        self.engineAvailabilityLayout.setObjectName("engineAvailabilityLayout")
        self.labelEngineAvailabilityHint = QLabel(self.groupEngineAvailability)
        self.labelEngineAvailabilityHint.setObjectName("labelEngineAvailabilityHint")
        self.labelEngineAvailabilityHint.setWordWrap(True)

        self.engineAvailabilityLayout.addWidget(self.labelEngineAvailabilityHint)

        self.treeEngineAvailability = QTreeWidget(self.groupEngineAvailability)
        self.treeEngineAvailability.setObjectName("treeEngineAvailability")
        self.treeEngineAvailability.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.treeEngineAvailability.setSelectionMode(QAbstractItemView.NoSelection)
        self.treeEngineAvailability.setRootIsDecorated(True)
        self.treeEngineAvailability.header().setStretchLastSection(True)

        self.engineAvailabilityLayout.addWidget(self.treeEngineAvailability)

        self.pageRecognitionLayout.addWidget(self.groupEngineAvailability)

        self.groupOfflineFeatures = QGroupBox(self.pageRecognition)
        self.groupOfflineFeatures.setObjectName("groupOfflineFeatures")
        self.offlineFeaturesLayout = QVBoxLayout(self.groupOfflineFeatures)
        self.offlineFeaturesLayout.setSpacing(6)
        self.offlineFeaturesLayout.setObjectName("offlineFeaturesLayout")
        self.labelOfflineFeaturesHint = QLabel(self.groupOfflineFeatures)
        self.labelOfflineFeaturesHint.setObjectName("labelOfflineFeaturesHint")
        self.labelOfflineFeaturesHint.setWordWrap(True)

        self.offlineFeaturesLayout.addWidget(self.labelOfflineFeaturesHint)

        self.treeOfflineFeatures = QTreeWidget(self.groupOfflineFeatures)
        self.treeOfflineFeatures.setObjectName("treeOfflineFeatures")
        self.treeOfflineFeatures.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.treeOfflineFeatures.setSelectionMode(QAbstractItemView.NoSelection)
        self.treeOfflineFeatures.setRootIsDecorated(False)
        self.treeOfflineFeatures.header().setStretchLastSection(True)

        self.offlineFeaturesLayout.addWidget(self.treeOfflineFeatures)

        self.btnInstallOfflineFeatures = QPushButton(self.groupOfflineFeatures)
        self.btnInstallOfflineFeatures.setObjectName("btnInstallOfflineFeatures")

        self.offlineFeaturesLayout.addWidget(self.btnInstallOfflineFeatures)

        self.pageRecognitionLayout.addWidget(self.groupOfflineFeatures)

        self.groupDownloadSources = QGroupBox(self.pageRecognition)
        self.groupDownloadSources.setObjectName("groupDownloadSources")
        self.downloadSourcesLayout = QVBoxLayout(self.groupDownloadSources)
        self.downloadSourcesLayout.setSpacing(8)
        self.downloadSourcesLayout.setObjectName("downloadSourcesLayout")
        self.labelDownloadSource = QLabel(self.groupDownloadSources)
        self.labelDownloadSource.setObjectName("labelDownloadSource")
        self.labelDownloadSource.setWordWrap(True)

        self.downloadSourcesLayout.addWidget(self.labelDownloadSource)

        self.btnSaveDownloadSources = QPushButton(self.groupDownloadSources)
        self.btnSaveDownloadSources.setObjectName("btnSaveDownloadSources")

        self.downloadSourcesLayout.addWidget(self.btnSaveDownloadSources)

        self.labelDownloadSourceStatus = QLabel(self.groupDownloadSources)
        self.labelDownloadSourceStatus.setObjectName("labelDownloadSourceStatus")
        self.labelDownloadSourceStatus.setWordWrap(True)

        self.downloadSourcesLayout.addWidget(self.labelDownloadSourceStatus)

        self.pageRecognitionLayout.addWidget(self.groupDownloadSources)

        self.spacerRecognitionPage = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )

        self.pageRecognitionLayout.addItem(self.spacerRecognitionPage)

        self.settingsStackedWidget.addWidget(self.pageRecognition)
        self.pageRuntime = QWidget()
        self.pageRuntime.setObjectName("pageRuntime")
        self.pageRuntimeLayout = QVBoxLayout(self.pageRuntime)
        self.pageRuntimeLayout.setSpacing(12)
        self.pageRuntimeLayout.setObjectName("pageRuntimeLayout")
        self.pageRuntimeLayout.setContentsMargins(16, 16, 16, 16)
        self.groupEnvMaintenance = QGroupBox(self.pageRuntime)
        self.groupEnvMaintenance.setObjectName("groupEnvMaintenance")
        self.envMaintenanceLayout = QVBoxLayout(self.groupEnvMaintenance)
        self.envMaintenanceLayout.setSpacing(8)
        self.envMaintenanceLayout.setObjectName("envMaintenanceLayout")
        self.backendOptionsContainer = QWidget(self.groupEnvMaintenance)
        self.backendOptionsContainer.setObjectName("backendOptionsContainer")
        self.backendOptionsContainerLayout = QVBoxLayout(self.backendOptionsContainer)
        self.backendOptionsContainerLayout.setSpacing(6)
        self.backendOptionsContainerLayout.setObjectName(
            "backendOptionsContainerLayout"
        )
        self.backendOptionsContainerLayout.setContentsMargins(0, 0, 0, 0)

        self.envMaintenanceLayout.addWidget(self.backendOptionsContainer)

        self.treeRuntimeStatus = QTreeWidget(self.groupEnvMaintenance)
        self.treeRuntimeStatus.setObjectName("treeRuntimeStatus")
        self.treeRuntimeStatus.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.treeRuntimeStatus.setSelectionMode(QAbstractItemView.NoSelection)
        self.treeRuntimeStatus.setRootIsDecorated(False)
        self.treeRuntimeStatus.header().setStretchLastSection(True)

        self.envMaintenanceLayout.addWidget(self.treeRuntimeStatus)

        self.labelEnvStatus = QLabel(self.groupEnvMaintenance)
        self.labelEnvStatus.setObjectName("labelEnvStatus")
        self.labelEnvStatus.setWordWrap(True)

        self.envMaintenanceLayout.addWidget(self.labelEnvStatus)

        self.treeDepsStatus = QTreeWidget(self.groupEnvMaintenance)
        self.treeDepsStatus.setObjectName("treeDepsStatus")
        self.treeDepsStatus.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.treeDepsStatus.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.treeDepsStatus.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.treeDepsStatus.setRootIsDecorated(True)
        self.treeDepsStatus.setExpandsOnDoubleClick(True)
        self.treeDepsStatus.header().setDefaultSectionSize(120)
        self.treeDepsStatus.header().setStretchLastSection(True)

        self.envMaintenanceLayout.addWidget(self.treeDepsStatus)

        self.envMaintenanceButtonsRow1 = QHBoxLayout()
        self.envMaintenanceButtonsRow1.setSpacing(8)
        self.envMaintenanceButtonsRow1.setObjectName("envMaintenanceButtonsRow1")
        self.btnReinstallPython = QPushButton(self.groupEnvMaintenance)
        self.btnReinstallPython.setObjectName("btnReinstallPython")

        self.envMaintenanceButtonsRow1.addWidget(self.btnReinstallPython)

        self.btnReinstallDeps = QPushButton(self.groupEnvMaintenance)
        self.btnReinstallDeps.setObjectName("btnReinstallDeps")

        self.envMaintenanceButtonsRow1.addWidget(self.btnReinstallDeps)

        self.btnReinstallSelected = QPushButton(self.groupEnvMaintenance)
        self.btnReinstallSelected.setObjectName("btnReinstallSelected")
        self.btnReinstallSelected.setEnabled(False)

        self.envMaintenanceButtonsRow1.addWidget(self.btnReinstallSelected)

        self.envMaintenanceSpacer1 = QSpacerItem(
            40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        self.envMaintenanceButtonsRow1.addItem(self.envMaintenanceSpacer1)

        self.envMaintenanceLayout.addLayout(self.envMaintenanceButtonsRow1)

        self.envMaintenanceButtonsRow2 = QHBoxLayout()
        self.envMaintenanceButtonsRow2.setSpacing(8)
        self.envMaintenanceButtonsRow2.setObjectName("envMaintenanceButtonsRow2")
        self.btnInstallMissing = QPushButton(self.groupEnvMaintenance)
        self.btnInstallMissing.setObjectName("btnInstallMissing")

        self.envMaintenanceButtonsRow2.addWidget(self.btnInstallMissing)

        self.btnUpdateDeps = QPushButton(self.groupEnvMaintenance)
        self.btnUpdateDeps.setObjectName("btnUpdateDeps")

        self.envMaintenanceButtonsRow2.addWidget(self.btnUpdateDeps)

        self.envMaintenanceSpacer2 = QSpacerItem(
            40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        self.envMaintenanceButtonsRow2.addItem(self.envMaintenanceSpacer2)

        self.envMaintenanceLayout.addLayout(self.envMaintenanceButtonsRow2)

        self.pageRuntimeLayout.addWidget(self.groupEnvMaintenance)

        self.groupCache = QGroupBox(self.pageRuntime)
        self.groupCache.setObjectName("groupCache")
        self.cacheLayout = QVBoxLayout(self.groupCache)
        self.cacheLayout.setSpacing(8)
        self.cacheLayout.setObjectName("cacheLayout")
        self.cacheButtonsLayout = QHBoxLayout()
        self.cacheButtonsLayout.setSpacing(8)
        self.cacheButtonsLayout.setObjectName("cacheButtonsLayout")
        self.btnRefreshCache = QPushButton(self.groupCache)
        self.btnRefreshCache.setObjectName("btnRefreshCache")

        self.cacheButtonsLayout.addWidget(self.btnRefreshCache)

        self.btnClearCache = QPushButton(self.groupCache)
        self.btnClearCache.setObjectName("btnClearCache")

        self.cacheButtonsLayout.addWidget(self.btnClearCache)

        self.horizontalSpacerCache = QSpacerItem(
            40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        self.cacheButtonsLayout.addItem(self.horizontalSpacerCache)

        self.cacheLayout.addLayout(self.cacheButtonsLayout)

        self.labelCacheStatus = QLabel(self.groupCache)
        self.labelCacheStatus.setObjectName("labelCacheStatus")
        self.labelCacheStatus.setWordWrap(True)

        self.cacheLayout.addWidget(self.labelCacheStatus)

        self.pageRuntimeLayout.addWidget(self.groupCache)

        self.spacerRuntimePage = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )

        self.pageRuntimeLayout.addItem(self.spacerRuntimePage)

        self.settingsStackedWidget.addWidget(self.pageRuntime)
        self.pageResidency = QWidget()
        self.pageResidency.setObjectName("pageResidency")
        self.pageResidencyLayout = QVBoxLayout(self.pageResidency)
        self.pageResidencyLayout.setSpacing(12)
        self.pageResidencyLayout.setObjectName("pageResidencyLayout")
        self.pageResidencyLayout.setContentsMargins(16, 16, 16, 16)
        self.groupPreload = QGroupBox(self.pageResidency)
        self.groupPreload.setObjectName("groupPreload")
        self.preloadLayout = QVBoxLayout(self.groupPreload)
        self.preloadLayout.setSpacing(8)
        self.preloadLayout.setObjectName("preloadLayout")
        self.chkEnablePreload = QCheckBox(self.groupPreload)
        self.chkEnablePreload.setObjectName("chkEnablePreload")
        self.chkEnablePreload.setChecked(True)

        self.preloadLayout.addWidget(self.chkEnablePreload)

        self.preloadOptions = QWidget(self.groupPreload)
        self.preloadOptions.setObjectName("preloadOptions")
        self.preloadOptionsLayout = QVBoxLayout(self.preloadOptions)
        self.preloadOptionsLayout.setSpacing(6)
        self.preloadOptionsLayout.setObjectName("preloadOptionsLayout")
        self.preloadOptionsLayout.setContentsMargins(20, 0, 0, 0)
        self.labelPreloadPipelines = QLabel(self.preloadOptions)
        self.labelPreloadPipelines.setObjectName("labelPreloadPipelines")

        self.preloadOptionsLayout.addWidget(self.labelPreloadPipelines)

        self.preloadPipelinesLayout = QHBoxLayout()
        self.preloadPipelinesLayout.setSpacing(4)
        self.preloadPipelinesLayout.setObjectName("preloadPipelinesLayout")
        self.chkPreload_OCR = QCheckBox(self.preloadOptions)
        self.chkPreload_OCR.setObjectName("chkPreload_OCR")
        self.chkPreload_OCR.setChecked(True)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_OCR)

        self.chkPreload_PP_STRUCTURE_V3 = QCheckBox(self.preloadOptions)
        self.chkPreload_PP_STRUCTURE_V3.setObjectName("chkPreload_PP_STRUCTURE_V3")
        self.chkPreload_PP_STRUCTURE_V3.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_PP_STRUCTURE_V3)

        self.chkPreload_PADDLEOCR_VL = QCheckBox(self.preloadOptions)
        self.chkPreload_PADDLEOCR_VL.setObjectName("chkPreload_PADDLEOCR_VL")
        self.chkPreload_PADDLEOCR_VL.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_PADDLEOCR_VL)

        self.chkPreload_TABLE_RECOGNITION = QCheckBox(self.preloadOptions)
        self.chkPreload_TABLE_RECOGNITION.setObjectName("chkPreload_TABLE_RECOGNITION")
        self.chkPreload_TABLE_RECOGNITION.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_TABLE_RECOGNITION)

        self.chkPreload_FORMULA_RECOGNITION = QCheckBox(self.preloadOptions)
        self.chkPreload_FORMULA_RECOGNITION.setObjectName(
            "chkPreload_FORMULA_RECOGNITION"
        )
        self.chkPreload_FORMULA_RECOGNITION.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_FORMULA_RECOGNITION)

        self.horizontalSpacerPreload = QSpacerItem(
            40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        self.preloadPipelinesLayout.addItem(self.horizontalSpacerPreload)

        self.preloadOptionsLayout.addLayout(self.preloadPipelinesLayout)

        self.preloadLayout.addWidget(self.preloadOptions)

        self.btnPreloadNow = QPushButton(self.groupPreload)
        self.btnPreloadNow.setObjectName("btnPreloadNow")
        self.btnPreloadNow.setMaximumSize(QSize(150, 16777215))

        self.preloadLayout.addWidget(self.btnPreloadNow)

        self.labelPreloadStatus = QLabel(self.groupPreload)
        self.labelPreloadStatus.setObjectName("labelPreloadStatus")
        self.labelPreloadStatus.setWordWrap(True)

        self.preloadLayout.addWidget(self.labelPreloadStatus)

        self.progressPreload = QProgressBar(self.groupPreload)
        self.progressPreload.setObjectName("progressPreload")
        self.progressPreload.setValue(0)
        self.progressPreload.setTextVisible(True)
        self.progressPreload.setVisible(False)

        self.preloadLayout.addWidget(self.progressPreload)

        self.pageResidencyLayout.addWidget(self.groupPreload)

        self.groupRuntimeCache = QGroupBox(self.pageResidency)
        self.groupRuntimeCache.setObjectName("groupRuntimeCache")
        self.runtimeCacheLayout = QVBoxLayout(self.groupRuntimeCache)
        self.runtimeCacheLayout.setSpacing(8)
        self.runtimeCacheLayout.setObjectName("runtimeCacheLayout")
        self.runtimeCacheButtonsLayout = QHBoxLayout()
        self.runtimeCacheButtonsLayout.setSpacing(8)
        self.runtimeCacheButtonsLayout.setObjectName("runtimeCacheButtonsLayout")
        self.btnRefreshPipelineCache = QPushButton(self.groupRuntimeCache)
        self.btnRefreshPipelineCache.setObjectName("btnRefreshPipelineCache")

        self.runtimeCacheButtonsLayout.addWidget(self.btnRefreshPipelineCache)

        self.btnReleaseHeavy = QPushButton(self.groupRuntimeCache)
        self.btnReleaseHeavy.setObjectName("btnReleaseHeavy")

        self.runtimeCacheButtonsLayout.addWidget(self.btnReleaseHeavy)

        self.btnReleaseAll = QPushButton(self.groupRuntimeCache)
        self.btnReleaseAll.setObjectName("btnReleaseAll")

        self.runtimeCacheButtonsLayout.addWidget(self.btnReleaseAll)

        self.horizontalSpacerRuntimeCacheButtons = QSpacerItem(
            40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        self.runtimeCacheButtonsLayout.addItem(self.horizontalSpacerRuntimeCacheButtons)

        self.runtimeCacheLayout.addLayout(self.runtimeCacheButtonsLayout)

        self.labelReleaseStatus = QLabel(self.groupRuntimeCache)
        self.labelReleaseStatus.setObjectName("labelReleaseStatus")
        self.labelReleaseStatus.setWordWrap(True)

        self.runtimeCacheLayout.addWidget(self.labelReleaseStatus)

        self.pageResidencyLayout.addWidget(self.groupRuntimeCache)

        self.spacerResidencyPage = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )

        self.pageResidencyLayout.addItem(self.spacerResidencyPage)

        self.settingsStackedWidget.addWidget(self.pageResidency)

        self.settingsHLayout.addWidget(self.settingsStackedWidget)

        self.tabWidget.addTab(self.tabSettings, "")

        self.verticalLayout.addWidget(self.tabWidget)

        self.retranslateUi(MainWindowWidget)

        self.tabWidget.setCurrentIndex(0)
        self.settingsNavList.setCurrentRow(0)
        self.settingsStackedWidget.setCurrentIndex(0)

        QMetaObject.connectSlotsByName(MainWindowWidget)

    # setupUi

    def retranslateUi(self, MainWindowWidget):
        self.labelResultTitle.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u8bc6\u522b\u7ed3\u679c", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnCopyRich.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u590d\u5236\u4e3a\u5bcc\u6587\u672c\u683c\u5f0f\uff0c\u53ef\u7c98\u8d34\u5230 Word/Excel \u4fdd\u7559\u8868\u683c\u683c\u5f0f",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnCopyRich.setText(
            QCoreApplication.translate("MainWindowWidget", "\u5bcc\u6587\u672c", None)
        )
        # if QT_CONFIG(tooltip)
        self.btnCopyMarkdown.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u590d\u5236\u4e3a Markdown \u683c\u5f0f\uff0c\u4fdd\u7559\u8868\u683c\u548c\u516c\u5f0f\u7ed3\u6784",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnCopyMarkdown.setText(
            QCoreApplication.translate("MainWindowWidget", "Markdown", None)
        )
        # if QT_CONFIG(tooltip)
        self.btnCopyPlain.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u590d\u5236\u4e3a\u7eaf\u6587\u672c\u683c\u5f0f",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnCopyPlain.setText(
            QCoreApplication.translate("MainWindowWidget", "\u7eaf\u6587\u672c", None)
        )
        self.tabWidget.setTabText(
            self.tabWidget.indexOf(self.tabOCR),
            QCoreApplication.translate(
                "MainWindowWidget", "\u5355\u6b21\u8bc6\u522b", None
            ),
        )

        __sortingEnabled = self.settingsNavList.isSortingEnabled()
        self.settingsNavList.setSortingEnabled(False)
        ___qlistwidgetitem = self.settingsNavList.item(0)
        ___qlistwidgetitem.setText(
            QCoreApplication.translate("MainWindowWidget", "\u901a\u7528", None)
        )
        ___qlistwidgetitem1 = self.settingsNavList.item(1)
        ___qlistwidgetitem1.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u8bc6\u522b\u8bbe\u7f6e", None
            )
        )
        ___qlistwidgetitem2 = self.settingsNavList.item(2)
        ___qlistwidgetitem2.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u8fd0\u884c\u65f6\u4e0e\u7ec4\u4ef6", None
            )
        )
        ___qlistwidgetitem3 = self.settingsNavList.item(3)
        ___qlistwidgetitem3.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u6a21\u578b\u7f13\u5b58", None
            )
        )
        self.settingsNavList.setSortingEnabled(__sortingEnabled)

        self.groupAppSettings.setTitle(
            QCoreApplication.translate("MainWindowWidget", "\u901a\u7528", None)
        )
        self.labelToolbarDesc.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u5728\u5c4f\u5e55\u8fb9\u7f18\u63d0\u4f9b\u622a\u56fe\u548c\u4e3b\u7a97\u53e3\u5feb\u6377\u5165\u53e3\uff0c\u53ef\u62d6\u52a8\u5230\u4efb\u610f\u8fb9\u7f18\u3002",
                None,
            )
        )
        # if QT_CONFIG(tooltip)
        self.chkShowToolbar.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u663e\u793a\u684c\u9762\u8fb9\u7f18\u6d6e\u52a8\u5de5\u5177\u680f\uff0c\u63d0\u4f9b\u5feb\u901f\u622a\u56fe\u548c\u4e3b\u7a97\u53e3\u5165\u53e3",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkShowToolbar.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u663e\u793a\u8fb9\u7f18\u5de5\u5177\u680f", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.chkAutoHideToolbar.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u5de5\u5177\u680f\u505c\u9760\u5728\u5c4f\u5e55\u8fb9\u7f18\u65f6\u81ea\u52a8\u9690\u85cf\uff0c\u9f20\u6807\u9760\u8fd1\u8fb9\u7f18\u65f6\u81ea\u52a8\u5f39\u51fa",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkAutoHideToolbar.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u81ea\u52a8\u9690\u85cf", None
            )
        )
        self.labelHideDelay.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u9690\u85cf\u5ef6\u8fdf:", None
            )
        )
        self.spinHideDelay.setSuffix(
            QCoreApplication.translate("MainWindowWidget", " \u6beb\u79d2", None)
        )
        self.labelPeekPixels.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u9690\u85cf\u65f6\u9732\u51fa:", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.spinPeekPixels.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u5de5\u5177\u680f\u81ea\u52a8\u9690\u85cf\u540e\u4ecd\u9732\u51fa\u5c4f\u5e55\u8fb9\u7f18\u7684\u50cf\u7d20\u5bbd\u5ea6\uff0c\u4fbf\u4e8e\u627e\u5230\u5e76\u5524\u51fa\u5de5\u5177\u680f",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.spinPeekPixels.setSuffix(
            QCoreApplication.translate("MainWindowWidget", " \u50cf\u7d20", None)
        )
        # if QT_CONFIG(tooltip)
        self.chkMinimizeToTray.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u5173\u95ed\u4e3b\u7a97\u53e3\u65f6\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8\u800c\u4e0d\u662f\u9000\u51fa\u7a0b\u5e8f",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkMinimizeToTray.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8",
                None,
            )
        )
        # if QT_CONFIG(tooltip)
        self.chkAutoStart.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u7cfb\u7edf\u542f\u52a8\u65f6\u81ea\u52a8\u8fd0\u884c VibeOCR",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkAutoStart.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u5f00\u673a\u81ea\u542f\u52a8", None
            )
        )
        self.groupEngineAvailability.setTitle(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u5f53\u524d\u53ef\u7528\u7684\u8bc6\u522b\u80fd\u529b",
                None,
            )
        )
        self.labelEngineAvailabilityHint.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u53ea\u8bfb\u6982\u89c8\u3002\u968f\u5305\u5de5\u5177\u65e0\u9700\u5b89\u88c5\uff1b\u9700\u8981\u989d\u5916\u7ec4\u4ef6\u7684\u80fd\u529b\u53ef\u5728\u4e0b\u65b9\u9009\u62e9\u5b89\u88c5\u3002",
                None,
            )
        )
        ___qtreewidgetitem = self.treeEngineAvailability.headerItem()
        ___qtreewidgetitem.setText(
            2, QCoreApplication.translate("MainWindowWidget", "\u8bf4\u660e", None)
        )
        ___qtreewidgetitem.setText(
            1, QCoreApplication.translate("MainWindowWidget", "\u72b6\u6001", None)
        )
        ___qtreewidgetitem.setText(
            0,
            QCoreApplication.translate(
                "MainWindowWidget", "\u8bc6\u522b\u6a21\u5f0f", None
            ),
        )
        self.groupOfflineFeatures.setTitle(
            QCoreApplication.translate(
                "MainWindowWidget", "\u53ef\u9009\u8bc6\u522b\u80fd\u529b", None
            )
        )
        self.labelOfflineFeaturesHint.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u4ec5\u5217\u51fa\u9700\u8981\u989d\u5916\u4e0b\u8f7d\u7684\u80fd\u529b\u3002\u52fe\u9009\u540e\u70b9\u51fb\u5b89\u88c5\uff0c\u5df2\u6709\u7684\u968f\u5305\u80fd\u529b\u4e0d\u4f1a\u51fa\u73b0\u5728\u8fd9\u91cc\u3002",
                None,
            )
        )
        ___qtreewidgetitem1 = self.treeOfflineFeatures.headerItem()
        ___qtreewidgetitem1.setText(
            1,
            QCoreApplication.translate(
                "MainWindowWidget", "\u5b89\u88c5\u72b6\u6001", None
            ),
        )
        ___qtreewidgetitem1.setText(
            0, QCoreApplication.translate("MainWindowWidget", "\u80fd\u529b", None)
        )
        self.btnInstallOfflineFeatures.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u5b89\u88c5\u6240\u9009\u80fd\u529b\u2026", None
            )
        )
        self.groupDownloadSources.setTitle(
            QCoreApplication.translate(
                "MainWindowWidget", "\u4e0b\u8f7d\u8bbe\u7f6e", None
            )
        )
        self.labelDownloadSource.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u4ec5\u5f71\u54cd\u540e\u7eed\u7ec4\u4ef6\u4e0b\u8f7d\u3002\u6bcf\u7c7b\u53ef\u9009\u4e00\u4e2a\uff1b\u7559\u7a7a\u65f6\u4f7f\u7528 Runtime \u9ed8\u8ba4\u6e90\u3002",
                None,
            )
        )
        self.btnSaveDownloadSources.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u4fdd\u5b58\u4e0b\u8f7d\u6e90", None
            )
        )
        self.labelDownloadSourceStatus.setText("")
        self.groupEnvMaintenance.setTitle(
            QCoreApplication.translate(
                "MainWindowWidget", "\u8fd0\u884c\u65f6\u4e0e\u7ec4\u4ef6", None
            )
        )
        ___qtreewidgetitem2 = self.treeRuntimeStatus.headerItem()
        ___qtreewidgetitem2.setText(
            1, QCoreApplication.translate("MainWindowWidget", "\u503c", None)
        )
        ___qtreewidgetitem2.setText(
            0, QCoreApplication.translate("MainWindowWidget", "\u9879\u76ee", None)
        )
        self.labelEnvStatus.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "Runtime \u72b6\u6001\uff1a\u68c0\u6d4b\u4e2d...",
                None,
            )
        )
        ___qtreewidgetitem3 = self.treeDepsStatus.headerItem()
        ___qtreewidgetitem3.setText(
            2, QCoreApplication.translate("MainWindowWidget", "\u7248\u672c", None)
        )
        ___qtreewidgetitem3.setText(
            1, QCoreApplication.translate("MainWindowWidget", "\u72b6\u6001", None)
        )
        ___qtreewidgetitem3.setText(
            0, QCoreApplication.translate("MainWindowWidget", "\u7ec4\u4ef6", None)
        )
        # if QT_CONFIG(tooltip)
        self.treeDepsStatus.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u5f53\u524d\u8fd0\u884c\u65f6\u4e0e\u7ec4\u4ef6\u72b6\u6001\u3002\u5c55\u5f00 Backend \u53ef\u67e5\u770b\u968f\u5305\u7ec4\u4ef6\uff1b\u5f02\u5e38\u9879\u53ef\u7528\u4e0b\u65b9\u7ef4\u62a4\u64cd\u4f5c\u4fee\u590d\u3002",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        # if QT_CONFIG(tooltip)
        self.btnReinstallPython.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u6821\u9a8c\u5e76\u4fee\u590d\u7ed1\u5b9a\u7248\u672c\u7684\u5b8c\u6574 Runtime profile\uff0c\u4e0d\u6267\u884c\u9010\u5305\u4f9d\u8d56\u53d8\u66f4\u3002",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnReinstallPython.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u91cd\u5efa\u5b8c\u6574 Runtime", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnReinstallDeps.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u9009\u62e9 CPU/GPU profile\uff1b\u786e\u8ba4\u540e\u901a\u8fc7\u53ef\u89c1\u5b89\u88c5\u6d41\u7a0b\u6821\u9a8c\u6216\u5207\u6362\u3002",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnReinstallDeps.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u9009\u62e9\u5e76\u786e\u4fdd Runtime profile",
                None,
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnReinstallSelected.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u6821\u9a8c\u5e76\u4fee\u590d\u5f53\u524d\u5b8c\u6574 Runtime profile\uff1b\u4e0d\u4f1a\u9010\u5305\u4fee\u6539\u3002",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnReinstallSelected.setText(
            QCoreApplication.translate("MainWindowWidget", "\u4fee\u590d Runtime", None)
        )
        # if QT_CONFIG(tooltip)
        self.btnInstallMissing.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u6821\u9a8c\u5f53\u524d profile\uff0c\u4ec5\u5728\u7f3a\u5931\u6216\u635f\u574f\u65f6\u4e0b\u8f7d\u5e76\u8865\u5168\u3002",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnInstallMissing.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u8865\u5168\u5f53\u524d Runtime", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnUpdateDeps.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "Runtime \u7248\u672c\u968f VibeOCR \u4ea7\u54c1\u66f4\u65b0\u7edf\u4e00\u5347\u7ea7\uff1b\u6b64\u5904\u53ea\u5237\u65b0 component-lock \u7ed1\u5b9a\u72b6\u6001\u3002",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnUpdateDeps.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u5237\u65b0\u4ea7\u54c1\u7ed1\u5b9a\u72b6\u6001",
                None,
            )
        )
        self.groupCache.setTitle(
            QCoreApplication.translate(
                "MainWindowWidget",
                "Runtime \u9a8c\u8bc1\u4e0e\u8bca\u65ad\u7f13\u5b58",
                None,
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnRefreshCache.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u91cd\u65b0\u9a8c\u8bc1\u4ea7\u54c1\u7ed1\u5b9a\u7684 Runtime manifest\u3001\u7ec4\u4ef6\u5b8c\u6574\u6027\u4e0e accelerator",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnRefreshCache.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u9a8c\u8bc1 Runtime \u72b6\u6001", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnClearCache.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u6e05\u9664 Classic \u542f\u52a8\u8bca\u65ad\u7f13\u5b58\uff08\u4e0d\u5f71\u54cd Runtime \u4e0e\u5df2\u4e0b\u8f7d\u6a21\u578b\uff09",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnClearCache.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u6e05\u9664\u5e94\u7528\u7f13\u5b58", None
            )
        )
        self.labelCacheStatus.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "Runtime \u72b6\u6001\uff1a\u5c1a\u672a\u9a8c\u8bc1",
                None,
            )
        )
        self.groupPreload.setTitle(
            QCoreApplication.translate(
                "MainWindowWidget", "\u6a21\u578b\u9884\u52a0\u8f7d", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.chkEnablePreload.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u542f\u52a8\u5e94\u7528\u65f6\u81ea\u52a8\u9884\u52a0\u8f7d\u9009\u4e2d\u7684\u7ba1\u9053\uff0c\u9996\u6b21\u8bc6\u522b\u65f6\u65e0\u9700\u7b49\u5f85",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkEnablePreload.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u542f\u52a8\u65f6\u81ea\u52a8\u9884\u52a0\u8f7d\u6a21\u578b",
                None,
            )
        )
        self.labelPreloadPipelines.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u9884\u52a0\u8f7d\u7ba1\u9053:", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.chkPreload_OCR.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u901a\u7528 OCR \u7ba1\u9053\uff08\u7ea6 600MB \u663e\u5b58\uff09",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkPreload_OCR.setText(
            QCoreApplication.translate("MainWindowWidget", "\u901a\u7528 OCR", None)
        )
        # if QT_CONFIG(tooltip)
        self.chkPreload_PP_STRUCTURE_V3.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "PP-StructureV3 \u6587\u6863\u7ed3\u6784\u5206\u6790\u7ba1\u9053",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkPreload_PP_STRUCTURE_V3.setText(
            QCoreApplication.translate("MainWindowWidget", "PP-StructureV3", None)
        )
        # if QT_CONFIG(tooltip)
        self.chkPreload_PADDLEOCR_VL.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "PaddleOCR-VL \u6587\u6863\u89e3\u6790\u7ba1\u9053",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkPreload_PADDLEOCR_VL.setText(
            QCoreApplication.translate("MainWindowWidget", "\u6587\u6863P", None)
        )
        # if QT_CONFIG(tooltip)
        self.chkPreload_TABLE_RECOGNITION.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget", "\u8868\u683c\u8bc6\u522b\u7ba1\u9053", None
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkPreload_TABLE_RECOGNITION.setText(
            QCoreApplication.translate("MainWindowWidget", "\u8868\u683c", None)
        )
        # if QT_CONFIG(tooltip)
        self.chkPreload_FORMULA_RECOGNITION.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget", "\u516c\u5f0f\u8bc6\u522b\u7ba1\u9053", None
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.chkPreload_FORMULA_RECOGNITION.setText(
            QCoreApplication.translate("MainWindowWidget", "\u516c\u5f0f", None)
        )
        # if QT_CONFIG(tooltip)
        self.btnPreloadNow.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u7acb\u5373\u9884\u52a0\u8f7d\u9009\u4e2d\u7684\u7ba1\u9053",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnPreloadNow.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u7acb\u5373\u9884\u52a0\u8f7d", None
            )
        )
        self.labelPreloadStatus.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u5c1a\u672a\u9884\u52a0\u8f7d", None
            )
        )
        self.groupRuntimeCache.setTitle(
            QCoreApplication.translate(
                "MainWindowWidget", "\u9a7b\u7559\u4e0e\u91ca\u653e", None
            )
        )
        self.btnRefreshPipelineCache.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u8bfb\u53d6\u9a7b\u7559\u72b6\u6001", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnRefreshPipelineCache.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u67e5\u8be2\u63a8\u7406\u8fdb\u7a0b\u5f53\u524d\u9a7b\u7559\u7684\u6a21\u578b\uff0c\u4e0d\u6539\u53d8\u4efb\u4f55\u72b6\u6001\uff08\u53ea\u8bfb\uff09",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnReleaseHeavy.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u91ca\u653e\u91cd\u6a21\u578b", None
            )
        )
        self.btnReleaseAll.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u91ca\u653e\u5168\u90e8\u6a21\u578b", None
            )
        )
        self.labelReleaseStatus.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u8fd0\u884c\u65f6\u7f13\u5b58\u72b6\u6001\uff1a\u670d\u52a1\u672a\u8fde\u63a5",
                None,
            )
        )
        self.tabWidget.setTabText(
            self.tabWidget.indexOf(self.tabSettings),
            QCoreApplication.translate("MainWindowWidget", "\u8bbe\u7f6e", None),
        )
        pass

    # retranslateUi
