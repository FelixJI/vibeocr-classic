# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_window.ui'
##
## Created by: Qt User Interface Compiler version 6.11.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton,
    QSizePolicy, QSpacerItem, QSpinBox, QSplitter,
    QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget)

from vibeocr.classic.widgets.preview_widget import PreviewWidget

class Ui_MainWindowWidget(object):
    def setupUi(self, MainWindowWidget):
        if not MainWindowWidget.objectName():
            MainWindowWidget.setObjectName(u"MainWindowWidget")
        self.verticalLayout = QVBoxLayout(MainWindowWidget)
        self.verticalLayout.setSpacing(0)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.tabWidget = QTabWidget(MainWindowWidget)
        self.tabWidget.setObjectName(u"tabWidget")
        self.tabOCR = QWidget()
        self.tabOCR.setObjectName(u"tabOCR")
        self.verticalLayout_ocr = QVBoxLayout(self.tabOCR)
        self.verticalLayout_ocr.setSpacing(0)
        self.verticalLayout_ocr.setObjectName(u"verticalLayout_ocr")
        self.verticalLayout_ocr.setContentsMargins(9, 9, 9, 9)
        self.ocrSplitter = QSplitter(self.tabOCR)
        self.ocrSplitter.setObjectName(u"ocrSplitter")
        self.ocrSplitter.setOrientation(Qt.Horizontal)
        self.previewWidget = PreviewWidget(self.ocrSplitter)
        self.previewWidget.setObjectName(u"previewWidget")
        self.previewWidget.setMinimumSize(QSize(300, 0))
        self.ocrSplitter.addWidget(self.previewWidget)
        self.resultPanel = QWidget(self.ocrSplitter)
        self.resultPanel.setObjectName(u"resultPanel")
        self.resultPanel.setMinimumSize(QSize(300, 0))
        self.verticalLayout_2 = QVBoxLayout(self.resultPanel)
        self.verticalLayout_2.setSpacing(6)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.headerLayout = QHBoxLayout()
        self.headerLayout.setObjectName(u"headerLayout")
        self.labelResultTitle = QLabel(self.resultPanel)
        self.labelResultTitle.setObjectName(u"labelResultTitle")

        self.headerLayout.addWidget(self.labelResultTitle)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.headerLayout.addItem(self.horizontalSpacer)


        self.verticalLayout_2.addLayout(self.headerLayout)

        self.copyButtonsLayout = QHBoxLayout()
        self.copyButtonsLayout.setSpacing(4)
        self.copyButtonsLayout.setObjectName(u"copyButtonsLayout")
        self.btnCopyRich = QPushButton(self.resultPanel)
        self.btnCopyRich.setObjectName(u"btnCopyRich")

        self.copyButtonsLayout.addWidget(self.btnCopyRich)

        self.btnCopyMarkdown = QPushButton(self.resultPanel)
        self.btnCopyMarkdown.setObjectName(u"btnCopyMarkdown")

        self.copyButtonsLayout.addWidget(self.btnCopyMarkdown)

        self.btnCopyPlain = QPushButton(self.resultPanel)
        self.btnCopyPlain.setObjectName(u"btnCopyPlain")

        self.copyButtonsLayout.addWidget(self.btnCopyPlain)


        self.verticalLayout_2.addLayout(self.copyButtonsLayout)

        self.ocrSplitter.addWidget(self.resultPanel)

        self.verticalLayout_ocr.addWidget(self.ocrSplitter)

        self.tabWidget.addTab(self.tabOCR, "")
        self.tabSettings = QWidget()
        self.tabSettings.setObjectName(u"tabSettings")
        self.settingsHLayout = QHBoxLayout(self.tabSettings)
        self.settingsHLayout.setSpacing(0)
        self.settingsHLayout.setObjectName(u"settingsHLayout")
        self.settingsHLayout.setContentsMargins(0, 0, 0, 0)
        self.settingsNavList = QListWidget(self.tabSettings)
        QListWidgetItem(self.settingsNavList)
        QListWidgetItem(self.settingsNavList)
        self.settingsNavList.setObjectName(u"settingsNavList")
        self.settingsNavList.setMinimumSize(QSize(150, 0))
        self.settingsNavList.setMaximumSize(QSize(180, 16777215))
        self.settingsNavList.setFrameShape(QFrame.NoFrame)
        self.settingsNavList.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settingsNavList.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.settingsHLayout.addWidget(self.settingsNavList)

        self.settingsStackedWidget = QStackedWidget(self.tabSettings)
        self.settingsStackedWidget.setObjectName(u"settingsStackedWidget")
        self.pageModelManagement = QWidget()
        self.pageModelManagement.setObjectName(u"pageModelManagement")
        self.pageModelLayout = QVBoxLayout(self.pageModelManagement)
        self.pageModelLayout.setSpacing(12)
        self.pageModelLayout.setObjectName(u"pageModelLayout")
        self.pageModelLayout.setContentsMargins(16, 16, 16, 16)
        self.groupPreload = QGroupBox(self.pageModelManagement)
        self.groupPreload.setObjectName(u"groupPreload")
        self.preloadLayout = QVBoxLayout(self.groupPreload)
        self.preloadLayout.setSpacing(8)
        self.preloadLayout.setObjectName(u"preloadLayout")
        self.chkEnablePreload = QCheckBox(self.groupPreload)
        self.chkEnablePreload.setObjectName(u"chkEnablePreload")
        self.chkEnablePreload.setChecked(True)

        self.preloadLayout.addWidget(self.chkEnablePreload)

        self.preloadOptions = QWidget(self.groupPreload)
        self.preloadOptions.setObjectName(u"preloadOptions")
        self.preloadOptionsLayout = QVBoxLayout(self.preloadOptions)
        self.preloadOptionsLayout.setSpacing(6)
        self.preloadOptionsLayout.setObjectName(u"preloadOptionsLayout")
        self.preloadOptionsLayout.setContentsMargins(20, 0, 0, 0)
        self.labelPreloadPipelines = QLabel(self.preloadOptions)
        self.labelPreloadPipelines.setObjectName(u"labelPreloadPipelines")

        self.preloadOptionsLayout.addWidget(self.labelPreloadPipelines)

        self.preloadPipelinesLayout = QHBoxLayout()
        self.preloadPipelinesLayout.setSpacing(4)
        self.preloadPipelinesLayout.setObjectName(u"preloadPipelinesLayout")
        self.chkPreload_OCR = QCheckBox(self.preloadOptions)
        self.chkPreload_OCR.setObjectName(u"chkPreload_OCR")
        self.chkPreload_OCR.setChecked(True)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_OCR)

        self.chkPreload_PP_STRUCTURE_V3 = QCheckBox(self.preloadOptions)
        self.chkPreload_PP_STRUCTURE_V3.setObjectName(u"chkPreload_PP_STRUCTURE_V3")
        self.chkPreload_PP_STRUCTURE_V3.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_PP_STRUCTURE_V3)

        self.chkPreload_PADDLEOCR_VL = QCheckBox(self.preloadOptions)
        self.chkPreload_PADDLEOCR_VL.setObjectName(u"chkPreload_PADDLEOCR_VL")
        self.chkPreload_PADDLEOCR_VL.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_PADDLEOCR_VL)

        self.chkPreload_TABLE_RECOGNITION = QCheckBox(self.preloadOptions)
        self.chkPreload_TABLE_RECOGNITION.setObjectName(u"chkPreload_TABLE_RECOGNITION")
        self.chkPreload_TABLE_RECOGNITION.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_TABLE_RECOGNITION)

        self.chkPreload_FORMULA_RECOGNITION = QCheckBox(self.preloadOptions)
        self.chkPreload_FORMULA_RECOGNITION.setObjectName(u"chkPreload_FORMULA_RECOGNITION")
        self.chkPreload_FORMULA_RECOGNITION.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreload_FORMULA_RECOGNITION)

        self.horizontalSpacerPreload = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.preloadPipelinesLayout.addItem(self.horizontalSpacerPreload)


        self.preloadOptionsLayout.addLayout(self.preloadPipelinesLayout)


        self.preloadLayout.addWidget(self.preloadOptions)

        self.btnPreloadNow = QPushButton(self.groupPreload)
        self.btnPreloadNow.setObjectName(u"btnPreloadNow")
        self.btnPreloadNow.setMaximumSize(QSize(150, 16777215))

        self.preloadLayout.addWidget(self.btnPreloadNow)

        self.labelPreloadStatus = QLabel(self.groupPreload)
        self.labelPreloadStatus.setObjectName(u"labelPreloadStatus")
        self.labelPreloadStatus.setWordWrap(True)

        self.preloadLayout.addWidget(self.labelPreloadStatus)

        self.progressPreload = QProgressBar(self.groupPreload)
        self.progressPreload.setObjectName(u"progressPreload")
        self.progressPreload.setValue(0)
        self.progressPreload.setTextVisible(True)
        self.progressPreload.setVisible(False)

        self.preloadLayout.addWidget(self.progressPreload)


        self.pageModelLayout.addWidget(self.groupPreload)

        self.groupRuntimeCache = QGroupBox(self.pageModelManagement)
        self.groupRuntimeCache.setObjectName(u"groupRuntimeCache")
        self.runtimeCacheLayout = QVBoxLayout(self.groupRuntimeCache)
        self.runtimeCacheLayout.setSpacing(8)
        self.runtimeCacheLayout.setObjectName(u"runtimeCacheLayout")
        self.runtimeCacheTtlLayout = QHBoxLayout()
        self.runtimeCacheTtlLayout.setObjectName(u"runtimeCacheTtlLayout")
        self.chkEnablePipelineTtl = QCheckBox(self.groupRuntimeCache)
        self.chkEnablePipelineTtl.setObjectName(u"chkEnablePipelineTtl")
        self.chkEnablePipelineTtl.setChecked(True)

        self.runtimeCacheTtlLayout.addWidget(self.chkEnablePipelineTtl)

        self.spinPipelineTtl = QSpinBox(self.groupRuntimeCache)
        self.spinPipelineTtl.setObjectName(u"spinPipelineTtl")
        self.spinPipelineTtl.setMinimum(1)
        self.spinPipelineTtl.setMaximum(1440)
        self.spinPipelineTtl.setValue(5)

        self.runtimeCacheTtlLayout.addWidget(self.spinPipelineTtl)

        self.horizontalSpacerRuntimeCacheTtl = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.runtimeCacheTtlLayout.addItem(self.horizontalSpacerRuntimeCacheTtl)


        self.runtimeCacheLayout.addLayout(self.runtimeCacheTtlLayout)

        self.runtimeCacheButtonsLayout = QHBoxLayout()
        self.runtimeCacheButtonsLayout.setSpacing(8)
        self.runtimeCacheButtonsLayout.setObjectName(u"runtimeCacheButtonsLayout")
        self.btnRefreshPipelineCache = QPushButton(self.groupRuntimeCache)
        self.btnRefreshPipelineCache.setObjectName(u"btnRefreshPipelineCache")

        self.runtimeCacheButtonsLayout.addWidget(self.btnRefreshPipelineCache)

        self.btnReleaseHeavy = QPushButton(self.groupRuntimeCache)
        self.btnReleaseHeavy.setObjectName(u"btnReleaseHeavy")

        self.runtimeCacheButtonsLayout.addWidget(self.btnReleaseHeavy)

        self.btnReleaseAll = QPushButton(self.groupRuntimeCache)
        self.btnReleaseAll.setObjectName(u"btnReleaseAll")

        self.runtimeCacheButtonsLayout.addWidget(self.btnReleaseAll)

        self.horizontalSpacerRuntimeCacheButtons = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.runtimeCacheButtonsLayout.addItem(self.horizontalSpacerRuntimeCacheButtons)


        self.runtimeCacheLayout.addLayout(self.runtimeCacheButtonsLayout)

        self.labelReleaseStatus = QLabel(self.groupRuntimeCache)
        self.labelReleaseStatus.setObjectName(u"labelReleaseStatus")
        self.labelReleaseStatus.setWordWrap(True)

        self.runtimeCacheLayout.addWidget(self.labelReleaseStatus)


        self.pageModelLayout.addWidget(self.groupRuntimeCache)

        self.groupCache = QGroupBox(self.pageModelManagement)
        self.groupCache.setObjectName(u"groupCache")
        self.cacheLayout = QVBoxLayout(self.groupCache)
        self.cacheLayout.setSpacing(8)
        self.cacheLayout.setObjectName(u"cacheLayout")
        self.cacheButtonsLayout = QHBoxLayout()
        self.cacheButtonsLayout.setSpacing(8)
        self.cacheButtonsLayout.setObjectName(u"cacheButtonsLayout")
        self.btnRefreshCache = QPushButton(self.groupCache)
        self.btnRefreshCache.setObjectName(u"btnRefreshCache")

        self.cacheButtonsLayout.addWidget(self.btnRefreshCache)

        self.btnClearCache = QPushButton(self.groupCache)
        self.btnClearCache.setObjectName(u"btnClearCache")

        self.cacheButtonsLayout.addWidget(self.btnClearCache)

        self.horizontalSpacerCache = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.cacheButtonsLayout.addItem(self.horizontalSpacerCache)


        self.cacheLayout.addLayout(self.cacheButtonsLayout)

        self.labelCacheStatus = QLabel(self.groupCache)
        self.labelCacheStatus.setObjectName(u"labelCacheStatus")
        self.labelCacheStatus.setForegroundRole(QPalette.PlaceholderText)

        self.cacheLayout.addWidget(self.labelCacheStatus)


        self.pageModelLayout.addWidget(self.groupCache)

        self.spacerModelPage = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.pageModelLayout.addItem(self.spacerModelPage)

        self.settingsStackedWidget.addWidget(self.pageModelManagement)
        self.pageAppSettings = QWidget()
        self.pageAppSettings.setObjectName(u"pageAppSettings")
        self.pageAppLayout = QVBoxLayout(self.pageAppSettings)
        self.pageAppLayout.setSpacing(12)
        self.pageAppLayout.setObjectName(u"pageAppLayout")
        self.pageAppLayout.setContentsMargins(16, 16, 16, 16)
        self.groupAppSettings = QGroupBox(self.pageAppSettings)
        self.groupAppSettings.setObjectName(u"groupAppSettings")
        self.appSettingsLayout = QVBoxLayout(self.groupAppSettings)
        self.appSettingsLayout.setSpacing(8)
        self.appSettingsLayout.setObjectName(u"appSettingsLayout")
        self.labelToolbarDesc = QLabel(self.groupAppSettings)
        self.labelToolbarDesc.setObjectName(u"labelToolbarDesc")
        self.labelToolbarDesc.setWordWrap(True)

        self.appSettingsLayout.addWidget(self.labelToolbarDesc)

        self.chkShowToolbar = QCheckBox(self.groupAppSettings)
        self.chkShowToolbar.setObjectName(u"chkShowToolbar")

        self.appSettingsLayout.addWidget(self.chkShowToolbar)

        self.toolbarSubOptions = QWidget(self.groupAppSettings)
        self.toolbarSubOptions.setObjectName(u"toolbarSubOptions")
        self.toolbarSubLayout = QVBoxLayout(self.toolbarSubOptions)
        self.toolbarSubLayout.setSpacing(8)
        self.toolbarSubLayout.setObjectName(u"toolbarSubLayout")
        self.toolbarSubLayout.setContentsMargins(20, 0, 0, 0)
        self.chkAutoHideToolbar = QCheckBox(self.toolbarSubOptions)
        self.chkAutoHideToolbar.setObjectName(u"chkAutoHideToolbar")

        self.toolbarSubLayout.addWidget(self.chkAutoHideToolbar)

        self.hideDelayLayout = QHBoxLayout()
        self.hideDelayLayout.setSpacing(8)
        self.hideDelayLayout.setObjectName(u"hideDelayLayout")
        self.labelHideDelay = QLabel(self.toolbarSubOptions)
        self.labelHideDelay.setObjectName(u"labelHideDelay")

        self.hideDelayLayout.addWidget(self.labelHideDelay)

        self.spinHideDelay = QSpinBox(self.toolbarSubOptions)
        self.spinHideDelay.setObjectName(u"spinHideDelay")
        self.spinHideDelay.setMinimum(100)
        self.spinHideDelay.setMaximum(5000)
        self.spinHideDelay.setSingleStep(100)
        self.spinHideDelay.setValue(500)

        self.hideDelayLayout.addWidget(self.spinHideDelay)

        self.hideDelaySpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.hideDelayLayout.addItem(self.hideDelaySpacer)


        self.toolbarSubLayout.addLayout(self.hideDelayLayout)


        self.appSettingsLayout.addWidget(self.toolbarSubOptions)

        self.chkMinimizeToTray = QCheckBox(self.groupAppSettings)
        self.chkMinimizeToTray.setObjectName(u"chkMinimizeToTray")

        self.appSettingsLayout.addWidget(self.chkMinimizeToTray)

        self.chkAutoStart = QCheckBox(self.groupAppSettings)
        self.chkAutoStart.setObjectName(u"chkAutoStart")

        self.appSettingsLayout.addWidget(self.chkAutoStart)


        self.pageAppLayout.addWidget(self.groupAppSettings)

        self.groupEnvMaintenance = QGroupBox(self.pageAppSettings)
        self.groupEnvMaintenance.setObjectName(u"groupEnvMaintenance")
        self.envMaintenanceLayout = QVBoxLayout(self.groupEnvMaintenance)
        self.envMaintenanceLayout.setSpacing(8)
        self.envMaintenanceLayout.setObjectName(u"envMaintenanceLayout")
        self.backendOptionsContainer = QWidget(self.groupEnvMaintenance)
        self.backendOptionsContainer.setObjectName(u"backendOptionsContainer")
        self.backendOptionsContainerLayout = QVBoxLayout(self.backendOptionsContainer)
        self.backendOptionsContainerLayout.setSpacing(6)
        self.backendOptionsContainerLayout.setObjectName(u"backendOptionsContainerLayout")
        self.backendOptionsContainerLayout.setContentsMargins(0, 0, 0, 0)

        self.envMaintenanceLayout.addWidget(self.backendOptionsContainer)

        self.labelEnvStatus = QLabel(self.groupEnvMaintenance)
        self.labelEnvStatus.setObjectName(u"labelEnvStatus")
        self.labelEnvStatus.setWordWrap(True)

        self.envMaintenanceLayout.addWidget(self.labelEnvStatus)

        self.treeDepsStatus = QTreeWidget(self.groupEnvMaintenance)
        self.treeDepsStatus.setObjectName(u"treeDepsStatus")
        self.treeDepsStatus.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.treeDepsStatus.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.treeDepsStatus.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.treeDepsStatus.setRootIsDecorated(True)
        self.treeDepsStatus.setExpandsOnDoubleClick(True)
        self.treeDepsStatus.header().setDefaultSectionSize(120)
        self.treeDepsStatus.header().setStretchLastSection(True)

        self.envMaintenanceLayout.addWidget(self.treeDepsStatus)

        self.btnReinstallSelected = QPushButton(self.groupEnvMaintenance)
        self.btnReinstallSelected.setObjectName(u"btnReinstallSelected")
        self.btnReinstallSelected.setEnabled(False)

        self.envMaintenanceLayout.addWidget(self.btnReinstallSelected)

        self.btnReinstallPython = QPushButton(self.groupEnvMaintenance)
        self.btnReinstallPython.setObjectName(u"btnReinstallPython")

        self.envMaintenanceLayout.addWidget(self.btnReinstallPython)

        self.btnReinstallDeps = QPushButton(self.groupEnvMaintenance)
        self.btnReinstallDeps.setObjectName(u"btnReinstallDeps")

        self.envMaintenanceLayout.addWidget(self.btnReinstallDeps)

        self.btnInstallMissing = QPushButton(self.groupEnvMaintenance)
        self.btnInstallMissing.setObjectName(u"btnInstallMissing")

        self.envMaintenanceLayout.addWidget(self.btnInstallMissing)

        self.btnUpdateDeps = QPushButton(self.groupEnvMaintenance)
        self.btnUpdateDeps.setObjectName(u"btnUpdateDeps")

        self.envMaintenanceLayout.addWidget(self.btnUpdateDeps)


        self.pageAppLayout.addWidget(self.groupEnvMaintenance)

        self.spacerAppPage = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.pageAppLayout.addItem(self.spacerAppPage)

        self.settingsStackedWidget.addWidget(self.pageAppSettings)

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
        self.labelResultTitle.setText(QCoreApplication.translate("MainWindowWidget", u"\u8bc6\u522b\u7ed3\u679c", None))
#if QT_CONFIG(tooltip)
        self.btnCopyRich.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u590d\u5236\u4e3a\u5bcc\u6587\u672c\u683c\u5f0f\uff0c\u53ef\u7c98\u8d34\u5230 Word/Excel \u4fdd\u7559\u8868\u683c\u683c\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.btnCopyRich.setText(QCoreApplication.translate("MainWindowWidget", u"\u5bcc\u6587\u672c", None))
#if QT_CONFIG(tooltip)
        self.btnCopyMarkdown.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u590d\u5236\u4e3a Markdown \u683c\u5f0f\uff0c\u4fdd\u7559\u8868\u683c\u548c\u516c\u5f0f\u7ed3\u6784", None))
#endif // QT_CONFIG(tooltip)
        self.btnCopyMarkdown.setText(QCoreApplication.translate("MainWindowWidget", u"Markdown", None))
#if QT_CONFIG(tooltip)
        self.btnCopyPlain.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u590d\u5236\u4e3a\u7eaf\u6587\u672c\u683c\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.btnCopyPlain.setText(QCoreApplication.translate("MainWindowWidget", u"\u7eaf\u6587\u672c", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tabOCR), QCoreApplication.translate("MainWindowWidget", u"\u5355\u6b21\u8bc6\u522b", None))

        __sortingEnabled = self.settingsNavList.isSortingEnabled()
        self.settingsNavList.setSortingEnabled(False)
        ___qlistwidgetitem = self.settingsNavList.item(0)
        ___qlistwidgetitem.setText(QCoreApplication.translate("MainWindowWidget", u"\u6a21\u578b\u7ba1\u7406", None))
        ___qlistwidgetitem1 = self.settingsNavList.item(1)
        ___qlistwidgetitem1.setText(QCoreApplication.translate("MainWindowWidget", u"\u5e94\u7528\u8bbe\u7f6e", None))
        self.settingsNavList.setSortingEnabled(__sortingEnabled)

        self.groupPreload.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u6a21\u578b\u9884\u52a0\u8f7d", None))
#if QT_CONFIG(tooltip)
        self.chkEnablePreload.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u52a8\u5e94\u7528\u65f6\u81ea\u52a8\u9884\u52a0\u8f7d\u9009\u4e2d\u7684\u7ba1\u9053\uff0c\u9996\u6b21\u8bc6\u522b\u65f6\u65e0\u9700\u7b49\u5f85", None))
#endif // QT_CONFIG(tooltip)
        self.chkEnablePreload.setText(QCoreApplication.translate("MainWindowWidget", u"\u542f\u52a8\u65f6\u81ea\u52a8\u9884\u52a0\u8f7d\u6a21\u578b", None))
        self.labelPreloadPipelines.setText(QCoreApplication.translate("MainWindowWidget", u"\u9884\u52a0\u8f7d\u7ba1\u9053:", None))
#if QT_CONFIG(tooltip)
        self.chkPreload_OCR.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u901a\u7528 OCR \u7ba1\u9053\uff08\u7ea6 600MB \u663e\u5b58\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreload_OCR.setText(QCoreApplication.translate("MainWindowWidget", u"\u901a\u7528 OCR", None))
#if QT_CONFIG(tooltip)
        self.chkPreload_PP_STRUCTURE_V3.setToolTip(QCoreApplication.translate("MainWindowWidget", u"PP-StructureV3 \u6587\u6863\u7ed3\u6784\u5206\u6790\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreload_PP_STRUCTURE_V3.setText(QCoreApplication.translate("MainWindowWidget", u"PP-StructureV3", None))
#if QT_CONFIG(tooltip)
        self.chkPreload_PADDLEOCR_VL.setToolTip(QCoreApplication.translate("MainWindowWidget", u"PaddleOCR-VL \u6587\u6863\u89e3\u6790\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreload_PADDLEOCR_VL.setText(QCoreApplication.translate("MainWindowWidget", u"\u6587\u6863P", None))
#if QT_CONFIG(tooltip)
        self.chkPreload_TABLE_RECOGNITION.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u8868\u683c\u8bc6\u522b\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreload_TABLE_RECOGNITION.setText(QCoreApplication.translate("MainWindowWidget", u"\u8868\u683c", None))
#if QT_CONFIG(tooltip)
        self.chkPreload_FORMULA_RECOGNITION.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u516c\u5f0f\u8bc6\u522b\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreload_FORMULA_RECOGNITION.setText(QCoreApplication.translate("MainWindowWidget", u"\u516c\u5f0f", None))
#if QT_CONFIG(tooltip)
        self.btnPreloadNow.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u7acb\u5373\u9884\u52a0\u8f7d\u9009\u4e2d\u7684\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.btnPreloadNow.setText(QCoreApplication.translate("MainWindowWidget", u"\u7acb\u5373\u9884\u52a0\u8f7d", None))
        self.labelPreloadStatus.setText(QCoreApplication.translate("MainWindowWidget", u"\u5c1a\u672a\u9884\u52a0\u8f7d", None))
        self.groupRuntimeCache.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u6a21\u578b\u8fd0\u884c\u7f13\u5b58", None))
#if QT_CONFIG(tooltip)
        self.chkEnablePipelineTtl.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u7528\u540e\uff0c\u8d85\u8fc7\u95f2\u7f6e\u65f6\u95f4\u7684\u91cd\u6a21\u578b\u4f1a\u5728\u63a8\u7406\u8fdb\u7a0b\u4e2d\u81ea\u52a8\u91ca\u653e", None))
#endif // QT_CONFIG(tooltip)
        self.chkEnablePipelineTtl.setText(QCoreApplication.translate("MainWindowWidget", u"\u81ea\u52a8\u91ca\u653e\u95f2\u7f6e\u6a21\u578b", None))
#if QT_CONFIG(tooltip)
        self.spinPipelineTtl.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u91cd\u6a21\u578b\u672a\u4f7f\u7528\u8d85\u8fc7\u8be5\u65f6\u95f4\u540e\u81ea\u52a8\u91ca\u653e", None))
#endif // QT_CONFIG(tooltip)
        self.spinPipelineTtl.setSuffix(QCoreApplication.translate("MainWindowWidget", u" \u5206\u949f", None))
        self.btnRefreshPipelineCache.setText(QCoreApplication.translate("MainWindowWidget", u"\u8bfb\u53d6\u9a7b\u7559\u72b6\u6001", None))
#if QT_CONFIG(tooltip)
        self.btnRefreshPipelineCache.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u67e5\u8be2\u63a8\u7406\u8fdb\u7a0b\u5f53\u524d\u9a7b\u7559\u7684\u6a21\u578b\uff0c\u4e0d\u6539\u53d8\u4efb\u4f55\u72b6\u6001\uff08\u53ea\u8bfb\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnReleaseHeavy.setText(QCoreApplication.translate("MainWindowWidget", u"\u91ca\u653e\u91cd\u6a21\u578b", None))
        self.btnReleaseAll.setText(QCoreApplication.translate("MainWindowWidget", u"\u91ca\u653e\u5168\u90e8\u6a21\u578b", None))
        self.labelReleaseStatus.setText(QCoreApplication.translate("MainWindowWidget", u"\u8fd0\u884c\u65f6\u7f13\u5b58\u72b6\u6001\uff1a\u670d\u52a1\u672a\u8fde\u63a5", None))
        self.groupCache.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u73af\u5883\u68c0\u6d4b\u7f13\u5b58", None))
#if QT_CONFIG(tooltip)
        self.btnRefreshCache.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u91cd\u65b0\u68c0\u6d4b Python\u3001\u4f9d\u8d56\u548c\u786c\u4ef6\u73af\u5883\u5e76\u66f4\u65b0\u7f13\u5b58", None))
#endif // QT_CONFIG(tooltip)
        self.btnRefreshCache.setText(QCoreApplication.translate("MainWindowWidget", u"\u91cd\u65b0\u68c0\u6d4b\u73af\u5883", None))
#if QT_CONFIG(tooltip)
        self.btnClearCache.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u6e05\u9664\u4f9d\u8d56\u68c0\u6d4b\u7f13\u5b58\uff08\u4e0d\u5f71\u54cd\u5df2\u4e0b\u8f7d\u7684\u6a21\u578b\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnClearCache.setText(QCoreApplication.translate("MainWindowWidget", u"\u6e05\u9664\u68c0\u6d4b\u7f13\u5b58", None))
        self.labelCacheStatus.setText(QCoreApplication.translate("MainWindowWidget", u"\u7f13\u5b58\u72b6\u6001: \u672a\u77e5", None))
        self.groupAppSettings.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u5e94\u7528\u8bbe\u7f6e", None))
        self.labelToolbarDesc.setText(QCoreApplication.translate("MainWindowWidget", u"\u8fb9\u7f18\u5de5\u5177\u680f\uff1a\u60ac\u6d6e\u5728\u5c4f\u5e55\u8fb9\u7f18\u7684\u5feb\u6377\u5de5\u5177\u6761\uff0c\u63d0\u4f9b\u4e00\u952e\u622a\u56fe\u548c\u547c\u51fa\u4e3b\u7a97\u53e3\u3002\u53ef\u62d6\u52a8\u5230\u5c4f\u5e55\u4efb\u610f\u8fb9\u7f18\uff0c\u505c\u9760\u540e\u81ea\u52a8\u9690\u85cf\uff0c\u9f20\u6807\u9760\u8fd1\u65f6\u5f39\u51fa\u3002", None))
#if QT_CONFIG(tooltip)
        self.chkShowToolbar.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u663e\u793a\u684c\u9762\u8fb9\u7f18\u6d6e\u52a8\u5de5\u5177\u680f\uff0c\u63d0\u4f9b\u5feb\u901f\u622a\u56fe\u548c\u4e3b\u7a97\u53e3\u5165\u53e3", None))
#endif // QT_CONFIG(tooltip)
        self.chkShowToolbar.setText(QCoreApplication.translate("MainWindowWidget", u"\u663e\u793a\u8fb9\u7f18\u5de5\u5177\u680f", None))
#if QT_CONFIG(tooltip)
        self.chkAutoHideToolbar.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5de5\u5177\u680f\u505c\u9760\u5728\u5c4f\u5e55\u8fb9\u7f18\u65f6\u81ea\u52a8\u9690\u85cf\uff0c\u9f20\u6807\u9760\u8fd1\u8fb9\u7f18\u65f6\u81ea\u52a8\u5f39\u51fa", None))
#endif // QT_CONFIG(tooltip)
        self.chkAutoHideToolbar.setText(QCoreApplication.translate("MainWindowWidget", u"\u81ea\u52a8\u9690\u85cf", None))
        self.labelHideDelay.setText(QCoreApplication.translate("MainWindowWidget", u"\u9690\u85cf\u5ef6\u8fdf:", None))
        self.spinHideDelay.setSuffix(QCoreApplication.translate("MainWindowWidget", u" \u6beb\u79d2", None))
#if QT_CONFIG(tooltip)
        self.chkMinimizeToTray.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5173\u95ed\u4e3b\u7a97\u53e3\u65f6\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8\u800c\u4e0d\u662f\u9000\u51fa\u7a0b\u5e8f", None))
#endif // QT_CONFIG(tooltip)
        self.chkMinimizeToTray.setText(QCoreApplication.translate("MainWindowWidget", u"\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8", None))
#if QT_CONFIG(tooltip)
        self.chkAutoStart.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u7cfb\u7edf\u542f\u52a8\u65f6\u81ea\u52a8\u8fd0\u884c VibeOCR", None))
#endif // QT_CONFIG(tooltip)
        self.chkAutoStart.setText(QCoreApplication.translate("MainWindowWidget", u"\u5f00\u673a\u81ea\u542f\u52a8", None))
        self.groupEnvMaintenance.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u63a8\u7406\u540e\u7aef\u4e0e\u4f9d\u8d56", None))
        self.labelEnvStatus.setText(QCoreApplication.translate("MainWindowWidget", u"Python \u8fd0\u884c\u65f6\uff1a\u68c0\u6d4b\u4e2d...", None))
        ___qtreewidgetitem = self.treeDepsStatus.headerItem()
        ___qtreewidgetitem.setText(2, QCoreApplication.translate("MainWindowWidget", u"\u7248\u672c", None))
        ___qtreewidgetitem.setText(1, QCoreApplication.translate("MainWindowWidget", u"\u72b6\u6001", None))
        ___qtreewidgetitem.setText(0, QCoreApplication.translate("MainWindowWidget", u"\u4f9d\u8d56", None))
#if QT_CONFIG(tooltip)
        self.treeDepsStatus.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5404 OCR \u4f9d\u8d56\u7684\u5b89\u88c5\u72b6\u6001\uff08\u4ec5\u4fbf\u643a\u6a21\u5f0f\u53ef\u89c1\uff09\u3002\u70b9\u51fb\u9876\u5c42\u5305\u5de6\u4fa7\u7bad\u5934\u5c55\u5f00\u5176\u95f4\u63a5\u4f9d\u8d56\uff1b\u6309\u4f4f Ctrl/Shift \u591a\u9009\u540e\u70b9\"\u91cd\u88c5\u9009\u4e2d\u9879\"\u6279\u91cf\u91cd\u88c5\u3002", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(tooltip)
        self.btnReinstallSelected.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u91cd\u88c5\u4e0a\u65b9\u4f9d\u8d56\u6811\u4e2d\u9009\u4e2d\u7684\u9876\u5c42\u5305\uff08\u6309 Ctrl/Shift \u591a\u9009\uff09\u3002\u95f4\u63a5\u4f9d\u8d56\u901a\u8fc7\u91cd\u88c5\u5176\u627f\u8f7d\u9876\u5c42\u5305\u4fee\u590d\uff0c\u65e0\u9700\u9010\u4e2a\u9009 leaf\u3002", None))
#endif // QT_CONFIG(tooltip)
        self.btnReinstallSelected.setText(QCoreApplication.translate("MainWindowWidget", u"\u91cd\u88c5\u9009\u4e2d\u9879", None))
#if QT_CONFIG(tooltip)
        self.btnReinstallPython.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u6821\u9a8c\u5e76\u4fee\u590d\u7ed1\u5b9a\u7248\u672c\u7684\u5b8c\u6574 Runtime profile\uff0c\u4e0d\u6267\u884c\u9010\u5305\u4f9d\u8d56\u53d8\u66f4\u3002", None))
#endif // QT_CONFIG(tooltip)
        self.btnReinstallPython.setText(QCoreApplication.translate("MainWindowWidget", u"\u4fee\u590d Runtime", None))
#if QT_CONFIG(tooltip)
        self.btnReinstallDeps.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u9009\u62e9 CPU/GPU profile\uff1b\u786e\u8ba4\u540e\u901a\u8fc7\u53ef\u89c1\u5b89\u88c5\u6d41\u7a0b\u6821\u9a8c\u6216\u5207\u6362\u3002", None))
#endif // QT_CONFIG(tooltip)
        self.btnReinstallDeps.setText(QCoreApplication.translate("MainWindowWidget", u"\u9009\u62e9\u5e76\u786e\u4fdd Runtime profile", None))
#if QT_CONFIG(tooltip)
        self.btnInstallMissing.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u6821\u9a8c\u5f53\u524d profile\uff0c\u4ec5\u5728\u7f3a\u5931\u6216\u635f\u574f\u65f6\u4e0b\u8f7d\u5e76\u8865\u5168\u3002", None))
#endif // QT_CONFIG(tooltip)
        self.btnInstallMissing.setText(QCoreApplication.translate("MainWindowWidget", u"\u8865\u5168\u5f53\u524d Runtime", None))
#if QT_CONFIG(tooltip)
        self.btnUpdateDeps.setToolTip(QCoreApplication.translate("MainWindowWidget", u"Runtime \u7248\u672c\u968f VibeOCR \u4ea7\u54c1\u66f4\u65b0\u7edf\u4e00\u5347\u7ea7\uff1b\u6b64\u5904\u53ea\u5237\u65b0 component-lock \u7ed1\u5b9a\u72b6\u6001\u3002", None))
#endif // QT_CONFIG(tooltip)
        self.btnUpdateDeps.setText(QCoreApplication.translate("MainWindowWidget", u"\u5237\u65b0\u4ea7\u54c1\u7ed1\u5b9a\u72b6\u6001", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tabSettings), QCoreApplication.translate("MainWindowWidget", u"\u8bbe\u7f6e", None))
        pass
    # retranslateUi

