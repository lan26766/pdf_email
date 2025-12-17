"""
PDF Fusion Pro - 激活服务器
主服务器文件
"""

import os
import json
import base64
import hashlib
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify
from flask_cors import CORS

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化Flask应用
app = Flask(__name__)
CORS(app)

# 配置类
class Config:
    """应用配置"""
    
    # 从环境变量读取
    ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')
    ADMIN_API_KEY = os.getenv('ADMIN_API_KEY', '')
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    
    # 邮件配置（可选）
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = os.getenv('SMTP_PORT', '587')
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    
    # Gumroad配置（可选）
    GUMROAD_WEBHOOK_SECRET = os.getenv('GUMROAD_WEBHOOK_SECRET', '')
    
    @classmethod
    def validate(cls):
        """验证必要配置"""
        required = ['ENCRYPTION_KEY', 'ADMIN_API_KEY']
        missing = []
        
        for var in required:
            if not getattr(cls, var):
                missing.append(var)
        
        if missing:
            logger.error(f"❌ 缺少必要配置: {', '.join(missing)}")
            return False
        
        if not cls.DATABASE_URL:
            logger.warning("⚠️  未配置 DATABASE_URL，将使用本地文件存储")
        
        logger.info("✅ 配置验证通过")
        return True

# 初始化配置
config = Config()

# ==================== 数据库初始化 ====================

def safe_init_database():
    """
    安全地初始化数据库
    如果失败，会降级到文件存储
    """
    if not config.DATABASE_URL:
        logger.info("💾 使用本地文件存储（未配置数据库）")
        return False
    
    try:
        # 尝试导入数据库模块
        from database.init_db import init_database
        
        logger.info("🔗 正在连接数据库...")
        success = init_database(config.DATABASE_URL)
        
        if success:
            logger.info("✅ 数据库初始化成功")
            return True
        else:
            logger.warning("⚠️  数据库初始化失败，降级到文件存储")
            return False
            
    except ImportError as e:
        logger.warning(f"⚠️  无法导入数据库模块: {e}")
        logger.warning("💾 降级到本地文件存储")
        return False
    except Exception as e:
        logger.error(f"❌ 数据库初始化异常: {e}")
        logger.warning("💾 降级到本地文件存储")
        return False

# ==================== 工具函数 ====================

def require_api_key(f):
    """API密钥验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != config.ADMIN_API_KEY:
            logger.warning(f"未授权访问尝试: {request.remote_addr}")
            return jsonify({"error": "未授权"}), 401
        return f(*args, **kwargs)
    return decorated_function

def generate_simple_activation_code(email, product_type="personal"):
    """生成简单的激活码"""
    import secrets
    
    # 生成随机部分
    random_part = secrets.token_hex(6).upper()
    
    # 产品类型代码
    type_codes = {'personal': 'P', 'business': 'B', 'enterprise': 'E'}
    type_code = type_codes.get(product_type, 'P')
    
    # 邮箱哈希
    email_hash = hashlib.md5(email.encode()).hexdigest()[:4].upper()
    
    # 时间戳（月日）
    timestamp = datetime.now().strftime('%m%d')
    
    # 组合激活码
    activation_code = f"PDF-{type_code}{timestamp}-{email_hash}-{random_part[:4]}-{random_part[4:8]}"
    
    # 计算有效期
    days_valid = 365
    if product_type == 'business':
        days_valid = 365 * 2
    elif product_type == 'enterprise':
        days_valid = 365 * 3
    
    # 激活数据
    activation_data = {
        "email": email,
        "product_type": product_type,
        "generated_at": datetime.now().isoformat(),
        "valid_until": (datetime.now() + timedelta(days=days_valid)).isoformat(),
        "max_devices": 3 if product_type == "personal" else 10,
        "days_valid": days_valid,
        "activation_code": activation_code
    }
    
    return activation_code, activation_data

def save_activation_record(email, activation_code, activation_data):
    """保存激活记录到数据库或文件"""
    try:
        if config.DATABASE_URL:
            # 尝试保存到数据库
            return save_to_database(email, activation_code, activation_data)
        else:
            # 保存到本地文件
            return save_to_file(email, activation_code, activation_data)
    except Exception as e:
        logger.error(f"保存记录失败: {e}")
        # 尝试文件备份
        return save_to_file(email, activation_code, activation_data)

def save_to_database(email, activation_code, activation_data):
    """保存到数据库"""
    try:
        import psycopg2
        import psycopg2.extras
        
        conn = psycopg2.connect(config.DATABASE_URL)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO activations 
        (email, activation_code, product_type, days_valid, max_devices, valid_until, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (activation_code) DO NOTHING
        ''', (
            email,
            activation_code,
            activation_data['product_type'],
            activation_data['days_valid'],
            activation_data['max_devices'],
            activation_data['valid_until'],
            json.dumps(activation_data)
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"💾 激活码保存到数据库: {activation_code[:20]}...")
        return True
        
    except ImportError:
        logger.warning("未安装 psycopg2，降级到文件存储")
        return save_to_file(email, activation_code, activation_data)
    except Exception as e:
        logger.error(f"数据库保存失败: {e}")
        return save_to_file(email, activation_code, activation_data)

def save_to_file(email, activation_code, activation_data):
    """保存到本地文件"""
    try:
        import csv
        from datetime import datetime
        
        filename = "activations.csv"
        file_exists = os.path.exists(filename)
        
        with open(filename, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['时间', '邮箱', '激活码', '产品类型', '有效期至', '最大设备数'])
            
            writer.writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                email,
                activation_code,
                activation_data['product_type'],
                activation_data['valid_until'][:10],
                activation_data['max_devices']
            ])
        
        logger.info(f"📄 激活码保存到文件: {activation_code}")
        return True
        
    except Exception as e:
        logger.error(f"文件保存失败: {e}")
        return False

# ==================== API 路由 ====================

@app.route('/')
def home():
    """主页"""
    storage_type = "数据库" if config.DATABASE_URL else "文件"
    
    return jsonify({
        "service": "PDF Fusion Pro 激活服务器",
        "version": "2.0.0",
        "status": "运行中",
        "timestamp": datetime.now().isoformat(),
        "storage": storage_type,
        "endpoints": {
            "health": "/health",
            "generate": "/api/generate",
            "verify": "/api/verify",
            "webhook": "/api/webhook/gumroad"
        }
    })

@app.route('/health')
def health_check():
    """健康检查"""
    try:
        # 测试数据库连接（如果配置了）
        db_status = "未配置"
        if config.DATABASE_URL:
            try:
                import psycopg2
                conn = psycopg2.connect(config.DATABASE_URL)
                conn.close()
                db_status = "连接正常"
            except:
                db_status = "连接失败"
        
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": db_status,
            "version": "2.0.0"
        })
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/generate', methods=['POST'])
@require_api_key
def api_generate():
    """生成激活码"""
    try:
        data = request.json
        
        # 验证输入
        email = data.get('email')
        if not email:
            return jsonify({"error": "邮箱地址是必需的"}), 400
        
        product_type = data.get('product_type', 'personal')
        days = data.get('days', 365)
        
        # 生成激活码
        activation_code, activation_data = generate_simple_activation_code(email, product_type)
        
        # 保存记录
        save_activation_record(email, activation_code, activation_data)
        
        logger.info(f"✅ 生成激活码: {email} -> {activation_code}")
        
        return jsonify({
            "success": True,
            "message": "激活码生成成功",
            "activation_code": activation_code,
            "data": activation_data
        })
        
    except Exception as e:
        logger.error(f"生成激活码失败: {e}")
        return jsonify({"error": "服务器错误"}), 500

@app.route('/api/verify', methods=['POST'])
def api_verify():
    """验证激活码"""
    try:
        data = request.json
        
        # 验证输入
        activation_code = data.get('activation_code')
        device_id = data.get('device_id', 'unknown')
        device_name = data.get('device_name', 'Unknown Device')
        
        if not activation_code:
            return jsonify({"error": "激活码是必需的"}), 400
        
        # 基本格式验证
        if not activation_code.startswith("PDF-"):
            return jsonify({
                "valid": False,
                "message": "无效的激活码格式"
            })
        
        # 提取产品类型
        product_type = 'personal'
        if len(activation_code) > 4:
            code_char = activation_code[4]
            if code_char == 'B':
                product_type = 'business'
            elif code_char == 'E':
                product_type = 'enterprise'
        
        # 模拟验证结果
        max_devices = 3 if product_type == "personal" else 10
        
        logger.info(f"✅ 验证激活码: {activation_code} -> {device_id}")
        
        return jsonify({
            "valid": True,
            "message": "激活成功",
            "data": {
                "product_type": product_type,
                "max_devices": max_devices,
                "valid_until": (datetime.now() + timedelta(days=365)).isoformat(),
                "device_id": device_id,
                "device_name": device_name
            }
        })
        
    except Exception as e:
        logger.error(f"验证激活码失败: {e}")
        return jsonify({"error": "服务器错误"}), 500

@app.route('/api/webhook/gumroad', methods=['POST'])
def webhook_gumroad():
    """处理Gumroad Webhook"""
    try:
        data = request.json
        
        # 获取基本信息
        email = data.get('email', '')
        product_name = data.get('product_name', '')
        
        if not email:
            return jsonify({"error": "邮箱地址缺失"}), 400
        
        logger.info(f"📨 收到Gumroad购买: {email} - {product_name}")
        
        # 判断产品类型
        product_name_lower = product_name.lower()
        product_type = 'personal'
        
        if 'business' in product_name_lower:
            product_type = 'business'
        elif 'enterprise' in product_name_lower:
            product_type = 'enterprise'
        
        # 生成激活码
        activation_code, activation_data = generate_simple_activation_code(email, product_type)
        
        # 保存购买记录
        try:
            import psycopg2
            conn = psycopg2.connect(config.DATABASE_URL)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT INTO purchases (purchase_id, email, product_name, gumroad_data)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (purchase_id) DO NOTHING
            ''', (
                data.get('id', ''),
                email,
                product_name,
                json.dumps(data)
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as db_error:
            logger.warning(f"保存购买记录失败: {db_error}")
            # 继续处理，不影响主要功能
        
        # 保存激活码
        save_activation_record(email, activation_code, activation_data)
        
        logger.info(f"✅ Webhook处理完成: {email} -> {activation_code}")
        
        return jsonify({
            "success": True,
            "message": "激活码已生成",
            "activation_code": activation_code,
            "email": email,
            "product_type": product_type
        })
        
    except Exception as e:
        logger.error(f"Webhook处理失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/activations', methods=['GET'])
@require_api_key
def list_activations():
    """列出激活码"""
    try:
        activations = []
        
        if config.DATABASE_URL:
            # 从数据库读取
            try:
                import psycopg2
                import psycopg2.extras
                
                conn = psycopg2.connect(config.DATABASE_URL)
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                
                cursor.execute('''
                SELECT email, activation_code, product_type, generated_at 
                FROM activations 
                ORDER BY generated_at DESC 
                LIMIT 50
                ''')
                
                activations = cursor.fetchall()
                conn.close()
                
            except Exception as db_error:
                logger.error(f"数据库查询失败: {db_error}")
        
        # 如果数据库为空或失败，尝试从文件读取
        if not activations:
            try:
                import csv
                filename = "activations.csv"
                
                if os.path.exists(filename):
                    with open(filename, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        activations = list(reader)
            except Exception as file_error:
                logger.error(f"文件读取失败: {file_error}")
        
        return jsonify({
            "success": True,
            "count": len(activations),
            "activations": activations,
            "source": "database" if config.DATABASE_URL else "file"
        })
        
    except Exception as e:
        logger.error(f"列出激活码失败: {e}")
        return jsonify({"error": str(e)}), 500

# ==================== 错误处理 ====================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "未找到请求的资源"}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "方法不允许"}), 405

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"服务器内部错误: {error}")
    return jsonify({"error": "服务器内部错误"}), 500

# ==================== 启动应用 ====================

# 初始化数据库
database_initialized = safe_init_database()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    
    logger.info("=" * 60)
    logger.info(f"🚀 启动 PDF Fusion Pro 激活服务器")
    logger.info(f"📅 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"🔑 管理员密钥: {config.ADMIN_API_KEY[:8]}...")
    logger.info(f"💾 存储方式: {'数据库' if database_initialized else '文件'}")
    logger.info(f"🌐 服务端口: {port}")
    logger.info("=" * 60)
    
    # 运行应用
    app.run(host='0.0.0.0', port=port, debug=False)