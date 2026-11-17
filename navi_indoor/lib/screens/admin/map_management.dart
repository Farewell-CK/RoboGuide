import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:camera/camera.dart';
import '../../services/supabase_service.dart';
import 'node_capture.dart';
import 'map_details_screen.dart';
import 'node_connection_screen.dart';
import '../localization_diagnostics_screen.dart';

class MapManagement extends StatefulWidget {
  final List<CameraDescription> cameras;

  const MapManagement({Key? key, required this.cameras}) : super(key: key);

  @override
  _MapManagementState createState() => _MapManagementState();
}

class _MapManagementState extends State<MapManagement> {
  final ImagePicker _picker = ImagePicker();
  final SupabaseService _supabaseService = SupabaseService();
  final TextEditingController _mapNameController = TextEditingController();

  File? _mapImage;
  String? _errorMessage;
  bool _isLoading = false;
  List<Map<String, dynamic>> _maps = [];
  Map<String, dynamic>? _selectedMap;
  bool _isAddingMap = false;

  @override
  void initState() {
    super.initState();
    _loadMaps();
  }

  @override
  void dispose() {
    _mapNameController.dispose();
    super.dispose();
  }

  Future<void> _pickImage() async {
    final XFile? image = await _picker.pickImage(
      source: ImageSource.gallery,
      maxWidth: 1920,
    );
    if (image != null) {
      setState(() {
        _mapImage = File(image.path);
      });
    }
  }

  Future<void> _saveMap() async {
    if (_mapImage == null) {
      setState(() {
        _errorMessage = 'Please select a map image first';
      });
      return;
    }

    if (_mapNameController.text.trim().isEmpty) {
      setState(() {
        _errorMessage = '请输入地图名称';
      });
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final String mapId = await _supabaseService.uploadMap(
        _mapNameController.text.trim(),
        _mapImage!,
      );

      if (mapId.isNotEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('地图保存成功！'),
            backgroundColor: Colors.green,
          ),
        );

        setState(() {
          _mapImage = null;
          _mapNameController.clear();
          _isAddingMap = false;
        });

        _loadMaps();
      }
    } catch (e) {
      setState(() {
        _errorMessage = 'Error saving map: ${e.toString()}';
      });
    } finally {
      setState(() {
        _isLoading = false;
      });
    }
  }

  Future<void> _loadMaps() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final maps = await _supabaseService.getMaps();
      setState(() {
        _maps = maps;
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _errorMessage = '加载地图失败： ${e.toString()}';
        _isLoading = false;
      });
    }
  }

  void _selectMap(Map<String, dynamic> map) {
    setState(() {
      _selectedMap = map;
    });
  }

  void _addNodeToMap() {
    if (_selectedMap == null) return;

    Navigator.of(context)
        .push(
          MaterialPageRoute(
            builder: (context) => NodeCapture(mapId: _selectedMap!['id']),
          ),
        )
        .then((result) {
          if (result == true) {
            _loadMaps();
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(
                content: Text('节点添加成功！'),
                backgroundColor: Colors.green,
              ),
            );
          }
        });
  }

  void _connectNodes() {
    if (_selectedMap == null) return;

    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (context) => NodeConnectionScreen(mapId: _selectedMap!['id']),
      ),
    );
  }

  void _viewMapDetails() {
    if (_selectedMap == null) return;

    Navigator.of(context)
        .push(
          MaterialPageRoute(
            builder: (context) => MapDetailsScreen(mapId: _selectedMap!['id']),
          ),
        )
        .then((_) {
          _loadMaps();
        });
  }

  void _cancelAddMap() {
    setState(() {
      _mapImage = null;
      _mapNameController.clear();
      _errorMessage = null;
      _isAddingMap = false;
    });
  }

  Future<void> _confirmDeleteMap(Map<String, dynamic> mapToDelete) async {
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) {
        return AlertDialog(
          title: const Text('确认删除'),
          content: Text(
            '确定要删除地图 "${mapToDelete['name'] ?? '未命名地图'}"及其全部节点和数据吗？此操作不可撤销。',
          ),
          actions: <Widget>[
            TextButton(
              child: const Text('取消'),
              onPressed: () {
                Navigator.of(context).pop(false);
              },
            ),
            TextButton(
              child: const Text('删除', style: TextStyle(color: Colors.red)),
              onPressed: () {
                Navigator.of(context).pop(true);
              },
            ),
          ],
        );
      },
    );

    if (confirmed == true) {
      _deleteMap(mapToDelete['id']);
    }
  }

  Future<void> _deleteMap(String mapId) async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      await _supabaseService.deleteMap(mapId);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('地图已删除'), backgroundColor: Colors.green),
      );
      setState(() {
        _selectedMap = null;
      });
      _loadMaps();
    } catch (e) {
      setState(() {
        _errorMessage = 'Error deleting map: ${e.toString()}';
        _isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final mediaQuery = MediaQuery.of(context);
    final screenHeight = mediaQuery.size.height;

    // Phone-optimized sizing with small phone adjustments
    final bool isSmallPhone = screenHeight < 600;
    final double buttonHeight = isSmallPhone ? 92.0 : 96.0;
    final double iconSize = 40.0;
    final double titleFontSize = isSmallPhone ? 18.0 : 20.0;
    final double subtitleFontSize = isSmallPhone ? 12.0 : 14.0;
    final double padding = 24.0;

    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        title: Semantics(label: 'Map management screen', child: Text('地图管理')),
        backgroundColor: Colors.black,
        elevation: 0,
        foregroundColor: Colors.white,
        actions: [
          IconButton(
            tooltip: '定位诊断与节点参考图',
            icon: const Icon(Icons.troubleshoot),
            onPressed:
                () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const LocalizationDiagnosticsScreen(),
                  ),
                ),
          ),
        ],
      ),
      body: SafeArea(
        child:
            _isLoading
                ? const Center(child: CircularProgressIndicator())
                : SingleChildScrollView(
                  padding: EdgeInsets.all(padding),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: _buildMainContent(
                      buttonHeight,
                      iconSize,
                      titleFontSize,
                      subtitleFontSize,
                      isSmallPhone,
                    ),
                  ),
                ),
      ),
    );
  }

  List<Widget> _buildMainContent(
    double buttonHeight,
    double iconSize,
    double titleFontSize,
    double subtitleFontSize,
    bool isSmallPhone,
  ) {
    return [
      // Error message (show only when not adding)
      if (_errorMessage != null && !_isAddingMap)
        Semantics(
          label: 'Error message',
          child: Container(
            padding: const EdgeInsets.all(16),
            margin: const EdgeInsets.only(bottom: 24),
            decoration: BoxDecoration(
              color: Colors.red.shade50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.red.shade600, width: 2),
            ),
            child: Row(
              children: [
                Semantics(
                  label: 'Error icon',
                  child: Icon(
                    Icons.error_outline,
                    color: Colors.red.shade600,
                    size: 24,
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    _errorMessage!,
                    style: TextStyle(
                      color: Colors.red.shade800,
                      fontSize: subtitleFontSize,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),

      // Available maps section header with action button
      Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Semantics(
            label: 'Available maps section',
            child: Text(
              '已有地图',
              style: TextStyle(
                fontSize: titleFontSize + 2,
                fontWeight: FontWeight.bold,
                color: Colors.black,
              ),
            ),
          ),
          if (!_isAddingMap)
            Semantics(
              label: 'Upload new map button',
              hint: 'Opens form to upload a new map',
              button: true,
              child: SizedBox(
                height: 40,
                child: ElevatedButton.icon(
                  onPressed: () {
                    setState(() {
                      _isAddingMap = true;
                      _errorMessage = null;
                    });
                  },
                  icon: Icon(Icons.add_photo_alternate, size: 18),
                  label: Text(
                    '上传地图',
                    style: TextStyle(fontSize: subtitleFontSize - 2),
                  ),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.green.shade600,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 8,
                    ),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8),
                    ),
                    elevation: 2,
                  ),
                ),
              ),
            ),
        ],
      ),

      const SizedBox(height: 16),

      // Conditionally Display Map List OR Add Form
      if (!_isAddingMap)
        ..._buildMapsList(
          buttonHeight,
          iconSize,
          titleFontSize,
          subtitleFontSize,
          isSmallPhone,
        )
      else
        _buildAddMapForm(
          buttonHeight,
          iconSize,
          titleFontSize,
          subtitleFontSize,
          isSmallPhone,
        ),
    ];
  }

  List<Widget> _buildMapsList(
    double buttonHeight,
    double iconSize,
    double titleFontSize,
    double subtitleFontSize,
    bool isSmallPhone,
  ) {
    if (_maps.isEmpty) {
      return [
        Semantics(
          label: '暂无地图',
          child: Center(
            child: Container(
              padding: EdgeInsets.all(isSmallPhone ? 40 : 48),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Semantics(
                    label: 'Empty maps icon',
                    child: Icon(
                      Icons.map_outlined,
                      size: 64,
                      color: Colors.grey.shade400,
                    ),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    '暂无地图',
                    style: TextStyle(
                      fontSize: titleFontSize,
                      fontWeight: FontWeight.bold,
                      color: Colors.grey.shade600,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    '请先上传一张地图',
                    style: TextStyle(
                      fontSize: subtitleFontSize,
                      color: Colors.grey.shade500,
                    ),
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
          ),
        ),
      ];
    }

    return [
      ListView.builder(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        itemCount: _maps.length,
        itemBuilder: (context, index) {
          final map = _maps[index];
          final bool isSelected =
              _selectedMap != null && _selectedMap!['id'] == map['id'];

          return Semantics(
            label:
                '${map['name'] ?? '未命名地图'} - ${map['node_count'] ?? 0} 个节点${isSelected ? ', selected' : ''}',
            hint:
                'Double tap to ${isSelected ? 'deselect' : 'select'} this map',
            button: true,
            selected: isSelected,
            child: Card(
              margin: const EdgeInsets.symmetric(vertical: 8.0),
              elevation: isSelected ? 4 : 2,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12.0),
                side:
                    isSelected
                        ? BorderSide(color: Colors.blue.shade600, width: 2)
                        : BorderSide.none,
              ),
              child: InkWell(
                onTap: () => _selectMap(map),
                borderRadius: BorderRadius.circular(12.0),
                child: Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              map['name'] ?? '未命名地图',
                              style: TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: titleFontSize,
                                color: Colors.black,
                              ),
                            ),
                          ),
                          if (isSelected)
                            Icon(
                              Icons.check_circle,
                              color: Theme.of(context).primaryColor,
                            ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Icon(
                            Icons.location_on,
                            size: 16,
                            color: Colors.grey.shade600,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            '${map['node_count'] ?? 0} 个节点',
                            style: TextStyle(
                              color: Colors.grey.shade600,
                              fontSize: subtitleFontSize,
                            ),
                          ),
                        ],
                      ),
                      if (isSelected) ...[
                        const SizedBox(height: 16),
                        // Action buttons
                        Column(
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: ElevatedButton.icon(
                                    onPressed: _addNodeToMap,
                                    icon: const Icon(
                                      Icons.add_location,
                                      size: 18,
                                    ),
                                    label: const Text('添加节点'),
                                    style: ElevatedButton.styleFrom(
                                      backgroundColor:
                                          Theme.of(context).primaryColor,
                                      foregroundColor: Colors.white,
                                      padding: const EdgeInsets.symmetric(
                                        vertical: 12,
                                      ),
                                      shape: RoundedRectangleBorder(
                                        borderRadius: BorderRadius.circular(8),
                                      ),
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 8),
                                Expanded(
                                  child: ElevatedButton.icon(
                                    onPressed: _connectNodes,
                                    icon: const Icon(Icons.link, size: 18),
                                    label: const Text('连接节点'),
                                    style: ElevatedButton.styleFrom(
                                      backgroundColor: Colors.orange,
                                      foregroundColor: Colors.white,
                                      padding: const EdgeInsets.symmetric(
                                        vertical: 12,
                                      ),
                                      shape: RoundedRectangleBorder(
                                        borderRadius: BorderRadius.circular(8),
                                      ),
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 8),
                            SizedBox(
                              width: double.infinity,
                              child: ElevatedButton.icon(
                                onPressed: _viewMapDetails,
                                icon: const Icon(Icons.manage_search, size: 18),
                                label: const Text('编辑节点与路线'),
                                style: ElevatedButton.styleFrom(
                                  backgroundColor: Colors.teal,
                                  foregroundColor: Colors.white,
                                  padding: const EdgeInsets.symmetric(
                                    vertical: 12,
                                  ),
                                  shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(8),
                                  ),
                                ),
                              ),
                            ),
                            const SizedBox(height: 8),
                            SizedBox(
                              width: double.infinity,
                              child: ElevatedButton.icon(
                                onPressed: () => _confirmDeleteMap(map),
                                icon: const Icon(
                                  Icons.delete_forever,
                                  size: 18,
                                ),
                                label: const Text('删除地图'),
                                style: ElevatedButton.styleFrom(
                                  backgroundColor: Colors.red[700],
                                  foregroundColor: Colors.white,
                                  padding: const EdgeInsets.symmetric(
                                    vertical: 12,
                                  ),
                                  shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(8),
                                  ),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            ),
          );
        },
      ),
    ];
  }

  Widget _buildAddMapForm(
    double buttonHeight,
    double iconSize,
    double titleFontSize,
    double subtitleFontSize,
    bool isSmallPhone,
  ) {
    return Card(
      elevation: 4,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  '添加地图',
                  style: TextStyle(
                    fontSize: titleFontSize,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: _cancelAddMap,
                  tooltip: '取消',
                  style: IconButton.styleFrom(
                    backgroundColor: Colors.grey[200],
                    shape: const CircleBorder(),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20.0),

            // Form error message
            if (_errorMessage != null)
              Container(
                padding: const EdgeInsets.all(12),
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Colors.red.shade100,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: Colors.red.shade300),
                ),
                child: Row(
                  children: [
                    Icon(Icons.error_outline, color: Colors.red.shade700),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        _errorMessage!,
                        style: TextStyle(
                          color: Colors.red.shade900,
                          fontSize: subtitleFontSize,
                        ),
                      ),
                    ),
                  ],
                ),
              ),

            TextField(
              controller: _mapNameController,
              decoration: InputDecoration(
                labelText: '地图名称',
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
                hintText: '例如：公司一层、办公室到电梯口',
                prefixIcon: const Icon(Icons.map),
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 16,
                ),
              ),
            ),
            const SizedBox(height: 20.0),

            // Image display for phones
            Center(
              child:
                  _mapImage != null
                      ? Stack(
                        alignment: Alignment.topRight,
                        children: [
                          Container(
                            constraints: BoxConstraints(
                              maxHeight: 280.0,
                              maxWidth: MediaQuery.of(context).size.width - 32,
                            ),
                            decoration: BoxDecoration(
                              borderRadius: BorderRadius.circular(12),
                              boxShadow: [
                                BoxShadow(
                                  color: Colors.black.withValues(alpha: 0.1),
                                  blurRadius: 8,
                                  offset: const Offset(0, 4),
                                ),
                              ],
                            ),
                            child: ClipRRect(
                              borderRadius: BorderRadius.circular(12),
                              child: Image.file(
                                _mapImage!,
                                fit: BoxFit.contain,
                              ),
                            ),
                          ),
                          Positioned(
                            top: 8,
                            right: 8,
                            child: IconButton(
                              icon: const Icon(
                                Icons.close,
                                color: Colors.white,
                              ),
                              onPressed: () => setState(() => _mapImage = null),
                              style: IconButton.styleFrom(
                                backgroundColor: Colors.black54,
                                shape: const CircleBorder(),
                              ),
                            ),
                          ),
                        ],
                      )
                      : Container(
                        height: 200.0,
                        width: double.infinity,
                        decoration: BoxDecoration(
                          border: Border.all(
                            color: Colors.grey[400]!,
                            width: 2,
                            style: BorderStyle.solid,
                          ),
                          borderRadius: BorderRadius.circular(12),
                          color: Colors.grey[50],
                        ),
                        child: InkWell(
                          onTap: _pickImage,
                          borderRadius: BorderRadius.circular(12),
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(
                                Icons.upload_file,
                                size: 64.0,
                                color: Colors.grey[600],
                              ),
                              const SizedBox(height: 12),
                              Text(
                                '选择地图图片',
                                style: TextStyle(
                                  fontSize: subtitleFontSize,
                                  fontWeight: FontWeight.w500,
                                  color: Colors.grey[700],
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                '点击选择文件',
                                style: TextStyle(
                                  fontSize: subtitleFontSize - 2,
                                  color: Colors.grey[500],
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
            ),
            const SizedBox(height: 24.0),

            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed:
                    _isLoading
                        ? null
                        : (_mapImage != null ? _saveMap : _pickImage),
                style: ElevatedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 16.0),
                  backgroundColor: Theme.of(context).primaryColor,
                  foregroundColor: Colors.white,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(8),
                  ),
                  elevation: 2,
                ),
                child:
                    _isLoading
                        ? SizedBox(
                          height: 20.0,
                          width: 20.0,
                          child: const CircularProgressIndicator(
                            strokeWidth: 3,
                            color: Colors.white,
                          ),
                        )
                        : Text(
                          _mapImage != null ? '保存地图' : '选择地图图片',
                          style: TextStyle(
                            fontSize: subtitleFontSize,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAccessibleButton({
    required BuildContext context,
    required IconData icon,
    required String title,
    required String subtitle,
    required Color backgroundColor,
    required VoidCallback onTap,
    required double buttonHeight,
    required double iconSize,
    required double titleFontSize,
    required double subtitleFontSize,
  }) {
    return Container(
      height: buttonHeight,
      width: double.infinity,
      decoration: BoxDecoration(
        color: backgroundColor,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.black, width: 2),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(16),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
            child: Row(
              children: [
                Icon(icon, color: Colors.white, size: iconSize),
                const SizedBox(width: 20),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(
                        title,
                        style: TextStyle(
                          fontSize: titleFontSize,
                          fontWeight: FontWeight.bold,
                          color: Colors.white,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        subtitle,
                        style: TextStyle(
                          fontSize: subtitleFontSize,
                          color: Colors.white.withOpacity(0.9),
                        ),
                      ),
                    ],
                  ),
                ),
                Icon(Icons.arrow_forward, color: Colors.white, size: 28),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
