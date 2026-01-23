"""
PDF Fusion Pro - 激活服务器
主服务器文件 - 完整版
支持 Gumroad Webhook (form-urlencoded 格式)
"""

import os
import json
import base64
import hashlib
import logging
import smtplib
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate
from urllib.parse import parse_qs, unquote

from flask import Flask, request, jsonify
from flask_cors import CORS
from cryptography.fernet import Fernet

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
    
    # 邮件配置
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = os.getenv('SMTP_PORT', '587')
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    
    # Gumroad配置
    GUMROAD_WEBHOOK_SECRET = os.getenv('GUMROAD_WEBHOOK_SECRET', '')
    
    # 服务器配置
    SERVER_PORT = os.getenv('PORT', '5000')
    SERVER_TIMEOUT = int(os.getenv('SERVER_TIMEOUT', '30'))  # 服务器超时时间（秒）
    REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '10'))  # 请求超时时间（秒）
    DEBUG_MODE = os.getenv('DEBUG', 'False').lower() == 'true'
    
    # 缓存配置
    CACHE_ENABLED = os.getenv('CACHE_ENABLED', 'True').lower() == 'true'
    CACHE_TTL = int(os.getenv('CACHE_TTL', '3600'))  # 缓存有效期（秒）
    
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

# 导入数据库连接池
from psycopg2 import pool

# 数据库连接池
db_pool = None

# 初始化配置
config = Config()

# 全局变量
app_start_time = time.time()
last_webhook_time = None
webhook_count = 0

# 速率限制配置
RATE_LIMITS = {
    'default': {'limit': 60, 'window': 60},  # 每分钟60个请求
    'verify': {'limit': 30, 'window': 60},    # 验证端点限制更严格
    'webhook': {'limit': 10, 'window': 60},   # Webhook限制
    'admin': {'limit': 100, 'window': 60}     # 管理端点宽松一些
}

# 存储请求记录
request_store = {}
request_store_lock = threading.Lock()

# 内存缓存
cache_store = {}
cache_lock = threading.Lock()

def get_cache(key):
    """获取缓存"""
    if not config.CACHE_ENABLED:
        return None
    
    with cache_lock:
        if key in cache_store:
            cached_data = cache_store[key]
            # 检查是否过期
            if time.time() < cached_data['expires_at']:
                logger.debug(f"缓存命中: {key}")
                return cached_data['value']
            else:
                # 清理过期缓存
                del cache_store[key]
                logger.debug(f"缓存过期: {key}")
                return None
        return None

def set_cache(key, value, ttl=None):
    """设置缓存"""
    if not config.CACHE_ENABLED:
        return False
    
    with cache_lock:
        ttl = ttl or config.CACHE_TTL
        cache_store[key] = {
            'value': value,
            'expires_at': time.time() + ttl,
            'created_at': time.time()
        }
        logger.debug(f"缓存设置: {key}, TTL: {ttl}s")
        return True

def clear_cache(key=None):
    """清除缓存"""
    if not config.CACHE_ENABLED:
        return False
    
    with cache_lock:
        if key:
            if key in cache_store:
                del cache_store[key]
                logger.debug(f"缓存清除: {key}")
                return True
            return False
        else:
            # 清除所有缓存
            cache_store.clear()
            logger.debug("所有缓存已清除")
            return True

def cleanup_cache():
    """清理过期缓存"""
    if not config.CACHE_ENABLED:
        return
    
    with cache_lock:
        expired_keys = []
        current_time = time.time()
        
        for key, cached_data in cache_store.items():
            if current_time >= cached_data['expires_at']:
                expired_keys.append(key)
        
        for key in expired_keys:
            del cache_store[key]
        
        if expired_keys:
            logger.debug(f"清理过期缓存: {len(expired_keys)} 个")

# 日志统计数据
request_stats = {
    'total_requests': 0,
    'endpoint_stats': {},
    'status_code_stats': {},
    'method_stats': {},
    'total_response_time': 0,
    'max_response_time': 0,
    'min_response_time': float('inf'),
    'last_reset_time': time.time()
}
stats_lock = threading.Lock()

def log_request(f):
    """请求日志记录装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 生成请求ID
        import uuid
        request_id = str(uuid.uuid4())
        
        # 记录请求开始时间
        start_time = time.time()
        
        # 记录请求信息
        client_ip = request.remote_addr
        method = request.method
        path = request.path
        user_agent = request.headers.get('User-Agent', 'Unknown')
        
        # 记录请求参数
        if request.method in ['POST', 'PUT', 'PATCH']:
            try:
                if request.is_json:
                    request_data = request.json
                else:
                    request_data = dict(request.form)
            except:
                request_data = "无法解析"
        else:
            request_data = dict(request.args)
        
        logger.info(f"📥 请求开始 [{request_id}]: {method} {path} from {client_ip}")
        logger.debug(f"请求参数: {request_data}")
        logger.debug(f"User-Agent: {user_agent}")
        
        try:
            # 执行请求处理
            response = f(*args, **kwargs)
            
            # 记录响应信息
            if isinstance(response, tuple):
                response_data, status_code = response
                if isinstance(response_data, dict):
                    response_size = len(str(response_data))
                else:
                    response_size = len(response_data.get_data() if hasattr(response_data, 'get_data') else str(response_data))
            else:
                status_code = 200
                response_size = len(response.get_data() if hasattr(response, 'get_data') else str(response))
            
            # 计算响应时间
            response_time = time.time() - start_time
            
            # 更新统计数据
            with stats_lock:
                request_stats['total_requests'] += 1
                request_stats['total_response_time'] += response_time
                
                if response_time > request_stats['max_response_time']:
                    request_stats['max_response_time'] = response_time
                if response_time < request_stats['min_response_time']:
                    request_stats['min_response_time'] = response_time
                
                # 端点统计
                if path not in request_stats['endpoint_stats']:
                    request_stats['endpoint_stats'][path] = {
                        'count': 0,
                        'total_time': 0,
                        'status_codes': {}
                    }
                request_stats['endpoint_stats'][path]['count'] += 1
                request_stats['endpoint_stats'][path]['total_time'] += response_time
                
                # 状态码统计
                if status_code not in request_stats['status_code_stats']:
                    request_stats['status_code_stats'][status_code] = 0
                request_stats['status_code_stats'][status_code] += 1
                
                # 端点状态码统计
                if status_code not in request_stats['endpoint_stats'][path]['status_codes']:
                    request_stats['endpoint_stats'][path]['status_codes'][status_code] = 0
                request_stats['endpoint_stats'][path]['status_codes'][status_code] += 1
                
                # 方法统计
                if method not in request_stats['method_stats']:
                    request_stats['method_stats'][method] = 0
                request_stats['method_stats'][method] += 1
            
            logger.info(f"📤 请求完成 [{request_id}]: {method} {path} -> {status_code} ({response_time:.3f}s, {response_size} bytes)")
            
            return response
            
        except Exception as e:
            # 记录异常
            response_time = time.time() - start_time
            logger.error(f"❌ 请求失败 [{request_id}]: {method} {path} -> {str(e)} ({response_time:.3f}s)")
            raise
    
    return decorated_function

def get_request_stats():
    """获取请求统计数据"""
    with stats_lock:
        stats_copy = request_stats.copy()
        
        # 计算平均响应时间
        if stats_copy['total_requests'] > 0:
            avg_response_time = stats_copy['total_response_time'] / stats_copy['total_requests']
        else:
            avg_response_time = 0
        
        # 格式化统计数据
        formatted_stats = {
            'total_requests': stats_copy['total_requests'],
            'average_response_time': round(avg_response_time, 3),
            'max_response_time': round(stats_copy['max_response_time'], 3),
            'min_response_time': round(stats_copy['min_response_time'], 3) if stats_copy['min_response_time'] != float('inf') else 0,
            'uptime_seconds': round(time.time() - stats_copy['last_reset_time'], 0),
            'status_codes': stats_copy['status_code_stats'],
            'methods': stats_copy['method_stats'],
            'endpoints': {}
        }
        
        # 格式化端点统计
        for endpoint, data in stats_copy['endpoint_stats'].items():
            endpoint_avg_time = data['total_time'] / data['count'] if data['count'] > 0 else 0
            formatted_stats['endpoints'][endpoint] = {
                'count': data['count'],
                'average_response_time': round(endpoint_avg_time, 3),
                'status_codes': data['status_codes']
            }
        
        return formatted_stats

def reset_request_stats():
    """重置请求统计数据"""
    with stats_lock:
        global request_stats
        request_stats = {
            'total_requests': 0,
            'endpoint_stats': {},
            'status_code_stats': {},
            'method_stats': {},
            'total_response_time': 0,
            'max_response_time': 0,
            'min_response_time': float('inf'),
            'last_reset_time': time.time()
        }
    logger.info("📊 请求统计数据已重置")

def init_professional_components():
    """初始化专业组件"""
    try:
        # 初始化激活码生成器
        encryption_key = config.ENCRYPTION_KEY
        if not encryption_key:
            logger.warning("⚠️  ENCRYPTION_KEY 未配置，将使用简单激活码")
            cipher = None
        else:
            # 确保密钥是字符串
            if isinstance(encryption_key, bytes):
                encryption_key = encryption_key.decode('utf-8')
            
            # 如果密钥不是有效的 base64，尝试修复
            if len(encryption_key) != 44 or '=' not in encryption_key[-1:]:
                logger.warning("⚠️  加密密钥格式可能不正确，尝试修复...")
                # 尝试 base64 编码
                try:
                    # 如果已经是字符串，先编码再解码
                    if isinstance(encryption_key, str):
                        encryption_key = base64.urlsafe_b64encode(encryption_key.encode()).decode()
                except:
                    logger.error("❌ 无法修复加密密钥，将使用简单激活码")
                    cipher = None
                else:
                    cipher = Fernet(encryption_key)
            else:
                cipher = Fernet(encryption_key)
            
            logger.info("✅ 加密组件初始化完成")
        
        # 初始化邮件发送器配置
        smtp_configured = all([
            config.SMTP_HOST,
            config.SMTP_USER,
            config.SMTP_PASSWORD
        ])
        
        if smtp_configured:
            logger.info(f"✅ 邮件服务已配置: {config.SMTP_USER}")
        else:
            logger.warning("⚠️  邮件服务未完全配置，将无法发送激活邮件")
        
        return cipher, smtp_configured
        
    except Exception as e:
        logger.error(f"❌ 专业组件初始化失败: {e}")
        return None, False

# 初始化专业组件
cipher, smtp_configured = init_professional_components()

def safe_init_database():
    """安全地初始化数据库"""
    global db_pool
    
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
            
            # 初始化连接池
            try:
                db_pool = pool.ThreadedConnectionPool(
                    minconn=2,  # 最小连接数
                    maxconn=10,  # 最大连接数
                    dsn=config.DATABASE_URL
                )
                logger.info("✅ 数据库连接池初始化成功")
            except Exception as pool_error:
                logger.error(f"❌ 连接池初始化失败: {pool_error}")
                logger.warning("⚠️  降级到单连接模式")
                db_pool = None
            
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

# 初始化数据库
database_initialized = safe_init_database()

# ==================== 工具函数 ====================

def parse_form_data(data):
    """解析 form-urlencoded 数据"""
    try:
        # 解析查询字符串
        parsed = parse_qs(data, keep_blank_values=True)
        
        # 将列表值转换为单个值，并解码 URL 编码
        result = {}
        for key, value in parsed.items():
            if isinstance(value, list):
                if len(value) == 1:
                    result[key] = unquote(value[0])
                else:
                    result[key] = [unquote(v) for v in value]
            else:
                result[key] = unquote(value)
        
        return result
    except Exception as e:
        logger.error(f"解析 form-data 失败: {e}")
        return {}

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

def rate_limit(limit_type='default'):
    """API速率限制装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            global request_store
            
            # 获取客户端IP
            client_ip = request.remote_addr
            
            # 获取限制配置
            limit_config = RATE_LIMITS.get(limit_type, RATE_LIMITS['default'])
            limit = limit_config['limit']
            window = limit_config['window']
            
            # 清理过期记录并检查限制
            current_time = time.time()
            
            with request_store_lock:
                # 确保客户端IP的记录存在
                if client_ip not in request_store:
                    request_store[client_ip] = []
                
                # 清理过期的请求记录
                request_store[client_ip] = [
                    timestamp for timestamp in request_store[client_ip]
                    if current_time - timestamp < window
                ]
                
                # 检查是否超过限制
                if len(request_store[client_ip]) >= limit:
                    logger.warning(f"速率限制触发: {client_ip}, 类型: {limit_type}")
                    return jsonify({
                        "error": "请求过于频繁，请稍后再试",
                        "limit": limit,
                        "window": window,
                        "retry_after": int(window - (current_time - min(request_store[client_ip])) + 1)
                    }), 429
                
                # 记录新请求
                request_store[client_ip].append(current_time)
                
                # 定期清理过期数据（防止内存泄漏）
                if len(request_store) > 1000:  # 当IP数量超过1000时清理
                    expired_ips = []
                    for ip, timestamps in request_store.items():
                        filtered = [t for t in timestamps if current_time - t < window]
                        if not filtered:
                            expired_ips.append(ip)
                        else:
                            request_store[ip] = filtered
                    
                    for ip in expired_ips:
                        del request_store[ip]
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def validate_request(content_types=None):
    """请求验证装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 验证Content-Type
            if content_types:
                content_type = request.content_type
                if not any(ct in content_type for ct in content_types):
                    return jsonify({
                        "error": f"不支持的Content-Type",
                        "supported_types": content_types,
                        "received_type": content_type
                    }), 415
            
            # 验证请求大小
            max_size = 1024 * 1024  # 1MB
            if request.content_length and request.content_length > max_size:
                return jsonify({
                    "error": "请求体过大",
                    "max_size": max_size,
                    "received_size": request.content_length
                }), 413
            
            # 验证请求方法
            if request.method in ['POST', 'PUT', 'PATCH']:
                try:
                    if request.is_json:
                        data = request.json
                        if data is None:
                            return jsonify({
                                "error": "请求体为空或格式错误"
                            }), 400
                except Exception as e:
                    return jsonify({
                        "error": "请求体格式错误",
                        "details": str(e)
                    }), 400
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_db_connection():
    """获取数据库连接"""
    global db_pool
    
    try:
        # 优先使用连接池
        if db_pool:
            conn = db_pool.getconn()
            logger.debug("从连接池获取连接")
            return conn
        else:
            # 回退到直接连接
            logger.warning("连接池不可用，使用直接连接")
            import psycopg2
            return psycopg2.connect(config.DATABASE_URL)
    except Exception as e:
        logger.error(f"获取数据库连接失败: {e}")
        # 再次尝试直接连接
        import psycopg2
        return psycopg2.connect(config.DATABASE_URL)

def put_db_connection(conn):
    """归还数据库连接"""
    global db_pool
    
    try:
        if db_pool:
            db_pool.putconn(conn)
            logger.debug("连接归还到连接池")
        else:
            conn.close()
    except Exception as e:
        logger.error(f"归还连接失败: {e}")
        # 如果是直接连接，手动关闭
        conn.close()

def error_response(code, message, details=None, request_id=None):
    """统一的错误响应函数"""
    error_response = {
        "error": message,
        "code": code,
        "timestamp": datetime.now().isoformat(),
        "path": request.path,
        "method": request.method
    }
    
    if details:
        error_response["details"] = details
    
    if request_id:
        error_response["request_id"] = request_id
    
    return jsonify(error_response), code

def generate_professional_activation_code(email, product_type="personal", 
                                         purchase_id="", product_name=""):
    """生成专业的激活码（使用Fernet加密）"""
    try:
        if not cipher:
            logger.warning("⚠️  加密组件未初始化，降级到简单激活码")
            return generate_simple_activation_code(email, product_type)
        
        # 根据产品类型设置参数
        days_valid = 365
        max_devices = 3
        
        if product_type == 'business':
            days_valid = 365 * 2
            max_devices = 10
        elif product_type == 'enterprise':
            days_valid = 365 * 3
            max_devices = 99
        elif product_type == 'professional':
            days_valid = 365
            max_devices = 5
        
        # 准备激活数据
        activation_data = {
            "email": email,
            "product_type": product_type,
            "days_valid": days_valid,
            "generated_at": datetime.now().isoformat(),
            "valid_until": (datetime.now() + timedelta(days=days_valid)).isoformat(),
            "max_devices": max_devices,
            "purchase_id": purchase_id,
            "product_name": product_name,
            "version": "2.0"
        }
        
        # 生成校验码
        checksum = hashlib.md5(
            f"{email}:{product_type}:{days_valid}:{purchase_id}".encode()
        ).hexdigest()[:8]
        activation_data['checksum'] = checksum
        
        # 加密
        data_str = json.dumps(activation_data, separators=(',', ':'))
        encrypted = cipher.encrypt(data_str.encode())
        
        # Base64编码
        activation_code = base64.urlsafe_b64encode(encrypted).decode()
        
        # 格式化为易读格式 (8位一组)
        formatted_code = '-'.join([
            activation_code[i:i+8] 
            for i in range(0, min(len(activation_code), 48), 8)
        ])
        
        # 确保不超过59字符
        if len(formatted_code) > 59:
            formatted_code = formatted_code[:59]
        
        logger.info(f"🔐 生成专业激活码: {formatted_code[:20]}...")
        return formatted_code, activation_data
        
    except Exception as e:
        logger.error(f"❌ 生成专业激活码失败: {e}")
        return generate_simple_activation_code(email, product_type)

def generate_simple_activation_code(email, product_type="personal"):
    """生成简单的激活码"""
    import secrets
    
    # 生成随机部分
    random_part = secrets.token_hex(6).upper()
    
    # 产品类型代码
    type_codes = {
        'personal': 'P', 
        'professional': 'R',
        'business': 'B', 
        'enterprise': 'E'
    }
    type_code = type_codes.get(product_type, 'P')
    
    # 邮箱哈希
    email_hash = hashlib.md5(email.encode()).hexdigest()[:4].upper()
    
    # 时间戳（月日）
    timestamp = datetime.now().strftime('%m%d')
    
    # 组合激活码
    activation_code = f"PDF-{type_code}{timestamp}-{email_hash}-{random_part[:4]}-{random_part[4:8]}"
    
    # 计算有效期
    days_valid = 365
    max_devices = 3
    
    if product_type == 'professional':
        max_devices = 5
    elif product_type == 'business':
        days_valid = 365 * 2
        max_devices = 10
    elif product_type == 'enterprise':
        days_valid = 365 * 3
        max_devices = 99
    
    # 激活数据
    activation_data = {
        "email": email,
        "product_type": product_type,
        "generated_at": datetime.now().isoformat(),
        "valid_until": (datetime.now() + timedelta(days=days_valid)).isoformat(),
        "max_devices": max_devices,
        "days_valid": days_valid,
        "activation_code": activation_code
    }
    
    return activation_code, activation_data

def send_activation_email(email, activation_code, activation_data):
    """Send activation email"""
    
    # Check email configuration
    if not all([config.SMTP_HOST, config.SMTP_USER, config.SMTP_PASSWORD]):
        logger.error("❌ Email service not configured, cannot send activation email")
        logger.info(f"📧 [Simulated] Activation email to: {email}")
        logger.info(f"   🔑 Activation code: {activation_code}")
        logger.info(f"   📅 Valid until: {activation_data.get('valid_until', 'N/A')}")
        return False
    
    try:
        # Extract information from activation data
        product_type = activation_data.get('product_type', 'personal').capitalize()
        valid_until = activation_data.get('valid_until', '')[:10]
        max_devices = activation_data.get('max_devices', 3)
        product_name = activation_data.get('product_name', 'PDF Fusion Pro')
        
        # Create email
        msg = MIMEMultipart('alternative')
        
        # Email headers
        subject = f"🎉 Your {product_name} {product_type} Edition Activation Code"
        msg['Subject'] = subject
        msg['From'] = f"PDF Fusion Pro Team <{config.SMTP_USER}>"
        msg['To'] = email
        msg['Date'] = formatdate(localtime=True)
        
        # HTML email content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{product_name} Activation Code</title>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; color: white; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: white; padding: 30px; border-radius: 0 0 10px 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                .code {{ background: #f8f9fa; border: 2px dashed #667eea; padding: 20px; text-align: center; font-family: monospace; font-size: 18px; letter-spacing: 2px; margin: 20px 0; border-radius: 5px; word-break: break-all; }}
                .info {{ background: #e7f3ff; border-left: 4px solid #1890ff; padding: 15px; margin: 20px 0; }}
                .warning {{ background: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .footer {{ text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; color: #666; font-size: 12px; }}
                table {{ width: 100%; border-collapse: collapse; }}
                td {{ padding: 8px 0; border-bottom: 1px solid #eee; }}
                td:first-child {{ font-weight: bold; width: 100px; color: #555; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1 style="margin: 0; font-size: 28px;">🎉 Thank you for purchasing {product_name}!</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">Your {product_type} Edition Activation Information</p>
            </div>
            
            <div class="content">
                <h2 style="color: #2c3e50; margin-top: 0;">📋 Activation Information</h2>
                
                <table>
                    <tr>
                        <td>Email Address</td>
                        <td>{email}</td>
                    </tr>
                    <tr>
                        <td>Product Edition</td>
                        <td>{product_type} Edition</td>
                    </tr>
                    <tr>
                        <td>Valid Until</td>
                        <td>{valid_until}</td>
                    </tr>
                    <tr>
                        <td>Supported Devices</td>
                        <td>{max_devices} devices</td>
                    </tr>
                </table>
                
                <h3 style="color: #2c3e50; margin-top: 30px;">🔑 Your Activation Code</h3>
                <div class="code">
                    {activation_code}
                </div>
                <p style="text-align: center; color: #666; font-size: 14px;">
                    Please copy this activation code and paste it in the software activation window
                </p>
                
                <div class="info">
                    <h4 style="margin-top: 0; color: #1890ff;">🚀 Activation Steps</h4>
                    <ol>
                        <li>Download and install {product_name}</li>
                        <li>Run the software, click the "Activate" button</li>
                        <li>Paste the activation code above</li>
                        <li>Click "Activate" to complete registration</li>
                    </ol>
                </div>
                
                <div class="warning">
                    <h4 style="margin-top: 0; color: #856404;">⚠️ Important Reminders</h4>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li>Each activation code can be used on up to <strong>{max_devices} devices</strong> simultaneously</li>
                        <li>Please keep this activation code safe, it cannot be recovered if lost</li>
                        <li>If you need to change devices, please deactivate from the original device first</li>
                        <li>Technical support email: getpdffusion7300@gmail.com</li>
                    </ul>
                </div>
            </div>
            
            <div class="footer">
                <p>© {datetime.now().year} {product_name}. All rights reserved.</p>
                <p>This email is automatically sent, please do not reply directly.</p>
            </div>
        </body>
        </html>
        """
        
        # Plain text content (fallback)
        text_content = f"""
Thank you for purchasing {product_name}!

Your activation information:
Email Address: {email}
Product Edition: {product_type} Edition
Valid Until: {valid_until}
Supported Devices: {max_devices} devices

Your activation code: {activation_code}

Activation Steps:
1. Download and install {product_name}
2. Run the software, click the "Activate" button
3. Paste the activation code above
4. Click "Activate" to complete registration

Important Reminders:
• Each activation code can be used on up to {max_devices} devices simultaneously
• Please keep this activation code safe, it cannot be recovered if lost
• If you need to change devices, please deactivate from the original device first
• Technical support email: support@example.com

© {datetime.now().year} {product_name}. All rights reserved.
This email is automatically sent, please do not reply directly.
        """
        
        # Add text and HTML versions
        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))
        
        # Connect to SMTP server and send
        logger.info(f"📤 Sending email to: {email}")
        
        with smtplib.SMTP(config.SMTP_HOST, int(config.SMTP_PORT)) as server:
            server.starttls()  # Enable secure connection
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"✅ Activation email successfully sent to: {email}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to send email: {e}")
        # Log simulated sending information for debugging
        logger.info(f"📧 [Failed Simulation] Activation email to: {email}")
        logger.info(f"   🔑 Activation code: {activation_code}")
        logger.info(f"   📅 Valid until: {activation_data.get('valid_until', 'N/A')}")
        return False

def save_activation_record(email, activation_code, activation_data):
    """保存激活记录到数据库或文件"""
    try:
        if config.DATABASE_URL:
            return save_to_database(email, activation_code, activation_data)
        else:
            return save_to_file(email, activation_code, activation_data)
    except Exception as e:
        logger.error(f"保存记录失败: {e}")
        return save_to_file(email, activation_code, activation_data)

def save_to_database(email, activation_code, activation_data):
    """保存到数据库"""
    try:
        import psycopg2
        import psycopg2.extras
        
        conn = get_db_connection()
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
        put_db_connection(conn)
        
        logger.info(f"💾 激活码保存到数据库: {activation_code[:20]}...")
        return True
        
    except Exception as e:
        logger.error(f"数据库保存失败: {e}")
        return save_to_file(email, activation_code, activation_data)

def verify_from_database(activation_code, device_id, device_name):
    """从数据库验证激活码"""
    try:
        import psycopg2
        import psycopg2.extras
        
        # 清理激活码格式
        activation_code = activation_code.replace('-', '').replace(' ', '')
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # 查询激活码
        cursor.execute('''
        SELECT * FROM activations WHERE activation_code = %s
        ''', (activation_code,))
        
        activation = cursor.fetchone()
        
        if not activation:
            cursor.close()
            put_db_connection(conn)
            return False, "激活码不存在", {}
        
        # 检查是否过期
        valid_until = activation['valid_until']
        if datetime.now() > valid_until:
            cursor.close()
            put_db_connection(conn)
            return False, "激活码已过期", {}
        
        # 检查设备限制
        cursor.execute('''
        SELECT COUNT(*) as device_count 
        FROM device_activations 
        WHERE activation_id = %s AND is_active = TRUE
        ''', (activation['id'],))
        
        device_count = cursor.fetchone()['device_count']
        
        if device_count >= activation['max_devices']:
            cursor.close()
            put_db_connection(conn)
            return False, f"已达到最大设备数限制 ({activation['max_devices']} 台)", {}
        
        # 检查设备是否已激活
        cursor.execute('''
        SELECT * FROM device_activations 
        WHERE activation_id = %s AND device_id = %s
        ''', (activation['id'], device_id))
        
        existing_device = cursor.fetchone()
        
        if existing_device:
            # 更新现有设备激活
            cursor.execute('''
            UPDATE device_activations 
            SET last_used = CURRENT_TIMESTAMP, is_active = TRUE
            WHERE id = %s
            ''', (existing_device['id'],))
        else:
            # 创建新设备激活
            cursor.execute('''
            INSERT INTO device_activations (activation_id, device_id, device_name)
            VALUES (%s, %s, %s)
            ''', (activation['id'], device_id, device_name))
        
        # 更新激活码状态
        cursor.execute('''
        UPDATE activations 
        SET is_used = TRUE, used_at = CURRENT_TIMESTAMP, used_by_device = %s
        WHERE id = %s
        ''', (device_id, activation['id']))
        
        conn.commit()
        cursor.close()
        put_db_connection(conn)
        
        # 计算剩余天数
        days_remaining = (valid_until - datetime.now()).days
        
        # 激活数据
        activation_data = {
            "product_type": activation['product_type'],
            "max_devices": activation['max_devices'],
            "valid_until": activation['valid_until'].isoformat(),
            "device_id": device_id,
            "device_name": device_name,
            "days_remaining": days_remaining,
            "email": activation['email'],
            "activation_id": activation['id']
        }
        
        return True, "激活成功", activation_data
        
    except Exception as e:
        logger.error(f"数据库验证失败: {e}")
        return False, f"数据库验证失败: {str(e)}", {}

def verify_from_file(activation_code, device_id, device_name):
    """从文件验证激活码"""
    try:
        filename = "activations.csv"
        
        if not os.path.exists(filename):
            return False, "激活码数据库不存在", {}
        
        # 清理激活码格式
        activation_code_clean = activation_code.replace('-', '').replace(' ', '')
        
        import csv
        
        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 清理文件中激活码的格式
                row_code = row['激活码']
                row_code_clean = row_code.replace('-', '').replace(' ', '')
                
                # 精确比较清理后的激活码
                if row_code_clean == activation_code_clean:
                    # 检查有效期
                    valid_until = datetime.fromisoformat(row['有效期至'])
                    if datetime.now() > valid_until:
                        return False, "激活码已过期", {}
                    
                    # 计算剩余天数
                    days_remaining = (valid_until - datetime.now()).days
                    
                    # 假设最大设备数为3
                    max_devices = 3
                    if row['产品类型'] == 'business':
                        max_devices = 10
                    elif row['产品类型'] == 'enterprise':
                        max_devices = 99
                    
                    # 激活数据
                    activation_data = {
                        "product_type": row['产品类型'],
                        "max_devices": max_devices,
                        "valid_until": valid_until.isoformat(),
                        "device_id": device_id,
                        "device_name": device_name,
                        "days_remaining": days_remaining,
                        "email": row['邮箱']
                    }
                    
                    return True, "激活成功", activation_data
        
        return False, "激活码不存在", {}
        
    except Exception as e:
        logger.error(f"文件验证失败: {e}")
        return False, f"文件验证失败: {str(e)}", {}

def save_to_file(email, activation_code, activation_data):
    """保存到本地文件"""
    try:
        import csv
        
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

# ==================== 心跳保持 ====================
def keep_service_awake():
    """定时访问服务防止休眠"""
    service_url = "https://pdf-email-1.onrender.com/health"
    
    while True:
        try:
            time.sleep(300)  # 每5分钟执行一次
            
            import requests
            response = requests.get(service_url, timeout=10)
            logger.info(f"💓 心跳保持: {response.status_code}")
            
            # 定期清理过期缓存
            cleanup_cache()
            
        except Exception as e:
            logger.error(f"心跳失败: {e}")
            # 即使心跳失败，也要清理缓存
            cleanup_cache()

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
        "email_configured": smtp_configured,
        "encryption_configured": cipher is not None,
        "endpoints": {
            "health": "/health",
            "status": "/api/status",
            "generate": "/api/generate",
            "verify": "/api/verify",
            "webhook": "/api/webhook/gumroad",
            "manual_activate": "/api/manual-activate",
            "debug_webhook": "/api/debug/webhook",
            "check_purchase": "/api/check-purchase/<sale_id>",
            "check_activation": "/api/check-activation/<activation_code>",
            "list_purchases": "/api/list-purchases",
            "list_activations": "/api/admin/activations"
        }
    })

@app.route('/health')
def health_check():
    """健康检查"""
    try:
        # 测试数据库连接
        db_status = "未配置"
        if config.DATABASE_URL:
            try:
                import psycopg2
                conn = psycopg2.connect(config.DATABASE_URL)
                conn.close()
                db_status = "连接正常"
            except Exception as e:
                logger.error(f"数据库连接失败: {e}")
                db_status = "连接失败"
        
        # 邮件服务状态
        email_status = "未配置"
        if smtp_configured:
            email_status = "已配置"
        
        # 加密状态
        encryption_status = "已启用" if cipher else "未启用"
        
        # 计算运行时间
        uptime = time.time() - app_start_time
        uptime_str = str(timedelta(seconds=int(uptime)))
        
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "uptime": uptime_str,
            "database": db_status,
            "email_service": email_status,
            "encryption": encryption_status,
            "version": "2.0.0",
            "webhook_count": webhook_count,
            "last_webhook": last_webhook_time
        })
        
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@rate_limit('default')
@app.route('/api/status', methods=['GET'])
def server_status():
    """服务器实时状态"""
    try:
        import psutil
        import socket
        
        status = {
            "server": {
                "hostname": socket.gethostname(),
                "uptime": time.time() - app_start_time,
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent
            },
            "service": {
                "webhook_endpoint": "/api/webhook/gumroad",
                "supported_content_types": ["application/json", "application/x-www-form-urlencoded"],
                "webhook_count": webhook_count,
                "last_webhook_time": last_webhook_time
            },
            "configuration": {
                "email_configured": smtp_configured,
                "encryption_configured": cipher is not None,
                "database_configured": bool(config.DATABASE_URL)
            },
            "urls": {
                "service": "https://pdf-email-1.onrender.com",
                "webhook": "https://pdf-email-1.onrender.com/api/webhook/gumroad",
                "health": "https://pdf-email-1.onrender.com/health"
            }
        }
        
        return jsonify(status)
        
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        return jsonify({"error": str(e)}), 500

# ==================== Gumroad Webhook 处理 ====================
@rate_limit('webhook')
@validate_request(['application/json', 'application/x-www-form-urlencoded'])
@app.route('/api/webhook/gumroad', methods=['POST'])
def webhook_gumroad():
    """处理Gumroad Webhook - 支持 form-urlencoded 格式"""
    global last_webhook_time, webhook_count
    
    try:
        last_webhook_time = datetime.now().isoformat()
        webhook_count += 1
        
        logger.info("=" * 60)
        logger.info(f"📨 🎯 收到 Gumroad Webhook 请求 #{webhook_count}")
        logger.info(f"📋 Content-Type: {request.content_type}")
        logger.info(f"📤 用户代理: {request.user_agent}")
        
        # 获取原始数据
        raw_data = request.get_data(as_text=True)
        logger.info(f"📄 原始数据长度: {len(raw_data)} 字符")
        
        # 解析数据
        data = {}
        
        if request.content_type == 'application/x-www-form-urlencoded':
            logger.info("🔄 解析 form-urlencoded 格式")
            data = parse_form_data(raw_data)
        elif request.content_type == 'application/json':
            logger.info("🔄 解析 JSON 格式")
            data = request.json
        else:
            # 尝试自动检测
            try:
                data = request.json
                logger.info("✅ 自动解析为 JSON")
            except:
                try:
                    data = parse_form_data(raw_data)
                    logger.info("✅ 自动解析为 form-urlencoded")
                except Exception as e:
                    logger.error(f"❌ 无法解析数据: {e}")
                    return jsonify({
                        "error": f"无法解析请求数据，Content-Type: {request.content_type}",
                        "supported_types": ["application/json", "application/x-www-form-urlencoded"]
                    }), 400
        
        if not data:
            logger.error("❌ 解析后数据为空")
            return jsonify({"error": "无法解析请求数据"}), 400
        
        # 日志数据内容
        logger.info(f"📊 解析后的数据字段: {list(data.keys())}")
        
        # 提取关键信息
        email = data.get('email')
        product_name = data.get('product_name', 'PDF Fusion Pro')
        sale_id = data.get('sale_id')
        order_number = data.get('order_number')
        
        logger.info(f"🔍 关键信息:")
        logger.info(f"   📧 Email: {email}")
        logger.info(f"   📦 Product: {product_name}")
        logger.info(f"   🆔 Sale ID: {sale_id}")
        logger.info(f"   🧾 Order: {order_number}")
        
        # 验证必要字段
        if not email:
            logger.error("❌ 缺少邮箱地址")
            return jsonify({"error": "邮箱地址缺失"}), 400
        
        # 确定产品类型
        product_name_lower = product_name.lower()
        product_type = 'personal'
        
        if 'business' in product_name_lower:
            product_type = 'business'
        elif 'enterprise' in product_name_lower:
            product_type = 'enterprise'
        elif 'professional' in product_name_lower:
            product_type = 'professional'
        
        logger.info(f"🏷️  产品类型: {product_type}")
        
        # 使用 sale_id 作为购买ID
        purchase_id = sale_id or order_number or f"gumroad_{int(datetime.now().timestamp())}"
        
        # 生成激活码
        logger.info(f"🔑 开始生成激活码...")
        activation_code, activation_data = generate_professional_activation_code(
            email=email,
            product_type=product_type,
            purchase_id=purchase_id,
            product_name=product_name
        )
        
        logger.info(f"✅ 激活码生成完成: {activation_code[:30]}...")
        
        # 保存购买记录到 purchases 表
        try:
            if config.DATABASE_URL:
                import psycopg2
                
                conn = psycopg2.connect(config.DATABASE_URL)
                cursor = conn.cursor()
                
                # 确保 purchases 表存在
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS purchases (
                    id SERIAL PRIMARY KEY,
                    purchase_id VARCHAR(255) UNIQUE,
                    email VARCHAR(255),
                    product_name VARCHAR(255),
                    gumroad_data JSONB,
                    processed BOOLEAN DEFAULT FALSE,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                
                # 插入购买记录
                cursor.execute('''
                INSERT INTO purchases (purchase_id, email, product_name, gumroad_data, processed)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (purchase_id) 
                DO UPDATE SET 
                    processed = TRUE,
                    processed_at = CURRENT_TIMESTAMP
                ''', (
                    purchase_id,
                    email,
                    product_name,
                    json.dumps(data)
                ))
                
                conn.commit()
                conn.close()
                logger.info(f"💾 购买记录保存成功: {purchase_id}")
                
        except Exception as db_error:
            logger.warning(f"保存购买记录失败: {db_error}")
            # 不影响主要功能，继续处理
        
        # 保存激活记录
        save_success = save_activation_record(email, activation_code, activation_data)
        
        # 发送邮件
        email_sent = False
        if activation_code:
            logger.info(f"📤 准备发送邮件到: {email}")
            email_sent = send_activation_email(email, activation_code, activation_data)
        
        # 记录处理结果
        logger.info("=" * 60)
        logger.info(f"🎉 Gumroad Webhook 处理完成")
        logger.info(f"   📧 邮箱: {email}")
        logger.info(f"   🏷️  产品: {product_name}")
        logger.info(f"   🔑 激活码: {activation_code[:20]}...")
        logger.info(f"   📤 邮件状态: {'✅ 已发送' if email_sent else '❌ 发送失败'}")
        logger.info(f"   💾 保存状态: {'✅ 成功' if save_success else '❌ 失败'}")
        logger.info("=" * 60)
        
        return jsonify({
            "success": True,
            "message": "激活码已生成" + ("并发送" if email_sent else "（但邮件发送失败）"),
            "activation_code": activation_code,
            "email": email,
            "product_type": product_type,
            "email_sent": email_sent,
            "save_success": save_success
        })
        
    except Exception as e:
        logger.error(f"❌ Webhook处理失败: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# ==================== 调试和监控端点 ====================
@app.route('/api/debug/webhook', methods=['POST'])
def debug_webhook():
    """调试Webhook - 显示原始数据"""
    try:
        logger.info("=" * 60)
        logger.info("🐛 调试 Webhook 请求")
        logger.info(f"📋 请求头: {dict(request.headers)}")
        
        raw_data = request.get_data(as_text=True)
        content_type = request.content_type
        
        result = {
            "method": request.method,
            "content_type": content_type,
            "raw_data": raw_data,
            "headers": dict(request.headers)
        }
        
        # 尝试解析
        if content_type == 'application/x-www-form-urlencoded':
            result['parsed_data'] = parse_form_data(raw_data)
        elif content_type == 'application/json':
            try:
                result['parsed_data'] = request.json
            except:
                result['parsed_data'] = "无法解析为JSON"
        else:
            result['parsed_data'] = "未知格式"
        
        logger.info(f"📊 解析结果: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}...")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"❌ 调试Webhook失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/check-purchase/<sale_id>', methods=['GET'])
def check_purchase(sale_id):
    """检查购买是否已处理"""
    try:
        logger.info(f"🔍 检查购买记录: {sale_id}")
        
        if not config.DATABASE_URL:
            return jsonify({
                "error": "数据库未配置",
                "sale_id": sale_id,
                "note": "无法检查购买记录"
            })
        
        import psycopg2
        import psycopg2.extras
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # 检查 purchases 表
        cursor.execute('''
        SELECT * FROM purchases WHERE purchase_id = %s
        ''', (sale_id,))
        purchase = cursor.fetchone()
        
        # 检查 activations 表
        cursor.execute('''
        SELECT email, activation_code, product_type, generated_at, metadata 
        FROM activations 
        WHERE metadata::jsonb->>'purchase_id' = %s 
           OR metadata::jsonb->>'sale_id' = %s
        ''', (sale_id, sale_id))
        activation = cursor.fetchone()
        
        cursor.close()
        put_db_connection(conn)
        
        return jsonify({
            "sale_id": sale_id,
            "purchase_record_found": bool(purchase),
            "activation_record_found": bool(activation),
            "purchase_details": purchase,
            "activation_details": activation,
            "checked_at": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"❌ 检查购买失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/check-activation/<activation_code>', methods=['GET'])
def check_activation(activation_code):
    """检查激活码详情"""
    try:
        if not config.DATABASE_URL:
            return jsonify({
                "error": "数据库未配置",
                "activation_code": activation_code
            })
        
        import psycopg2
        import psycopg2.extras
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute('''
        SELECT * FROM activations WHERE activation_code = %s
        ''', (activation_code,))
        
        activation = cursor.fetchone()
        cursor.close()
        put_db_connection(conn)
        
        if activation:
            return jsonify({
                "found": True,
                "activation": activation
            })
        else:
            return jsonify({
                "found": False,
                "activation_code": activation_code,
                "message": "未找到该激活码"
            })
        
    except Exception as e:
        logger.error(f"❌ 检查激活码失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/list-purchases', methods=['GET'])
@require_api_key
def list_purchases():
    """列出所有购买记录"""
    try:
        if not config.DATABASE_URL:
            return jsonify({
                "error": "数据库未配置",
                "note": "使用文件存储，无法列出购买记录"
            })
        
        import psycopg2
        import psycopg2.extras
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute('''
        SELECT 
            purchase_id, 
            email, 
            product_name, 
            processed, 
            processed_at, 
            created_at,
            LENGTH(gumroad_data::text) as data_length
        FROM purchases 
        ORDER BY processed_at DESC 
        LIMIT 50
        ''')
        
        purchases = cursor.fetchall()
        cursor.close()
        put_db_connection(conn)
        
        return jsonify({
            "success": True,
            "count": len(purchases),
            "purchases": purchases
        })
        
    except Exception as e:
        logger.error(f"❌ 列出购买记录失败: {e}")
        return jsonify({"error": str(e)}), 500

# ==================== 管理端点 ====================
@app.route('/api/generate', methods=['POST'])
@require_api_key
@rate_limit('admin')
@validate_request(['application/json'])
def api_generate():
    """生成激活码"""
    try:
        data = request.json
        
        # 验证输入
        email = data.get('email')
        if not email:
            return jsonify({"error": "邮箱地址是必需的"}), 400
        
        product_type = data.get('product_type', 'personal')
        
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

@rate_limit('verify')
@validate_request(['application/json'])
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
        
        # 清理激活码格式
        code_clean = activation_code.replace('-', '').replace(' ', '')
        
        # 验证激活码
        if config.DATABASE_URL and database_initialized:
            # 从数据库验证
            valid, message, activation_data = verify_from_database(code_clean, device_id, device_name)
            
            # 如果数据库验证失败，回退到文件验证
            if not valid:
                logger.warning(f"数据库验证失败，回退到文件验证: {message}")
                valid, message, activation_data = verify_from_file(code_clean, device_id, device_name)
        else:
            # 从文件验证
            valid, message, activation_data = verify_from_file(code_clean, device_id, device_name)
        
        if not valid:
            logger.warning(f"❌ 激活码验证失败: {activation_code} -> {message}")
            return jsonify({
                "valid": False,
                "message": message,
                "data": {}
            })
        
        # 记录验证成功
        logger.info(f"✅ 验证激活码: {activation_code} -> {device_id}")
        
        return jsonify({
            "valid": True,
            "message": "激活成功",
            "data": activation_data
        })
        
    except Exception as e:
        logger.error(f"验证激活码失败: {e}")
        return jsonify({"error": "服务器错误"}), 500

@rate_limit('default')
@validate_request(['application/json'])
@app.route('/api/manual-activate', methods=['POST'])
def manual_activate():
    """手动触发激活（用于测试和调试）"""
    try:
        logger.info("🛠️  收到手动激活请求")
        
        data = request.json
        
        # 验证必要字段
        required_fields = ['email', 'product_name']
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            return jsonify({
                "error": f"缺少必要字段: {', '.join(missing_fields)}",
                "required_fields": required_fields,
                "received_fields": list(data.keys())
            }), 400
        
        email = data['email']
        product_name = data['product_name']
        
        # 使用提供的购买ID或生成一个
        purchase_id = data.get('purchase_id', f"manual_{int(datetime.now().timestamp())}")
        
        # 判断产品类型
        product_name_lower = product_name.lower()
        product_type = 'personal'
        
        if 'business' in product_name_lower:
            product_type = 'business'
        elif 'enterprise' in product_name_lower:
            product_type = 'enterprise'
        elif 'professional' in product_name_lower:
            product_type = 'professional'
        
        logger.info(f"🛠️  手动激活参数:")
        logger.info(f"   📧 邮箱: {email}")
        logger.info(f"   🏷️  产品: {product_name} ({product_type})")
        logger.info(f"   🆔 购买ID: {purchase_id}")
        
        # 生成激活码
        activation_code, activation_data = generate_professional_activation_code(
            email=email,
            product_type=product_type,
            purchase_id=purchase_id,
            product_name=product_name
        )
        
        # 保存激活码
        save_success = save_activation_record(email, activation_code, activation_data)
        
        # 发送邮件
        email_sent = False
        if activation_code:
            email_sent = send_activation_email(email, activation_code, activation_data)
        
        return jsonify({
            "success": True,
            "message": "手动激活成功",
            "activation_code": activation_code,
            "email": email,
            "product_name": product_name,
            "product_type": product_type,
            "purchase_id": purchase_id,
            "email_sent": email_sent,
            "save_success": save_success,
            "note": "这是手动触发的激活"
        })
        
    except Exception as e:
        logger.error(f"❌ 手动激活失败: {e}")
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
                
                conn = get_db_connection()
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                
                cursor.execute('''
                SELECT email, activation_code, product_type, generated_at 
                FROM activations 
                ORDER BY generated_at DESC 
                LIMIT 50
                ''')
                
                activations = cursor.fetchall()
                cursor.close()
                put_db_connection(conn)
                
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
    logger.warning(f"404 错误: {request.path}")
    return error_response(404, "未找到请求的资源", details={"requested_path": request.path})

@app.errorhandler(405)
def method_not_allowed(error):
    allowed_methods = request.url_rule.methods if request.url_rule else []
    return error_response(405, "方法不允许", details={"allowed_methods": list(allowed_methods)})

@app.errorhandler(400)
def bad_request(error):
    logger.warning(f"400 错误: {error}")
    return error_response(400, "请求参数错误", details={"error": str(error)})

@app.errorhandler(401)
def unauthorized(error):
    logger.warning(f"401 错误: 未授权访问")
    return error_response(401, "未授权访问", details={"realm": "PDF Fusion Pro 激活服务器"})

@app.errorhandler(403)
def forbidden(error):
    logger.warning(f"403 错误: 禁止访问")
    return error_response(403, "禁止访问", details={"path": request.path})

@app.errorhandler(415)
def unsupported_media_type(error):
    return error_response(415, "不支持的媒体类型", details={"content_type": request.content_type})

@app.errorhandler(429)
def too_many_requests(error):
    return error_response(429, "请求过于频繁", details={"retry_after": "60"})

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"服务器内部错误: {error}", exc_info=True)
    return error_response(500, "服务器内部错误", details={"error_type": str(type(error).__name__)})

# ==================== 启动应用 ====================
if __name__ == '__main__':
    port = int(config.SERVER_PORT)
    
    logger.info("=" * 60)
    logger.info(f"🚀 启动 PDF Fusion Pro 激活服务器")
    logger.info(f"📅 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"🔑 管理员密钥: {config.ADMIN_API_KEY[:8]}...")
    logger.info(f"🔐 加密组件: {'已启用' if cipher else '未启用'}")
    logger.info(f"📧 邮件服务: {'已配置' if smtp_configured else '未配置'}")
    logger.info(f"💾 存储方式: {'数据库' if database_initialized else '文件'}")
    logger.info(f"🌐 服务端口: {port}")
    logger.info(f"⏱️  服务器超时: {config.SERVER_TIMEOUT}秒")
    logger.info(f"⏱️  请求超时: {config.REQUEST_TIMEOUT}秒")
    logger.info(f"� 缓存配置: {'已启用' if config.CACHE_ENABLED else '未启用'} (TTL: {config.CACHE_TTL}秒)")
    logger.info(f"🐛 调试模式: {'开启' if config.DEBUG_MODE else '关闭'}")
    logger.info(f"�� Webhook地址: http://0.0.0.0:{port}/api/webhook/gumroad")
    logger.info(f"🌍 公网地址: https://pdf-email-1.onrender.com/api/webhook/gumroad")
    logger.info("=" * 60)
    
    # 启动心跳线程
    heartbeat_thread = threading.Thread(target=keep_service_awake, daemon=True)
    heartbeat_thread.start()
    logger.info("💓 心跳保持线程已启动")
    
    # 运行应用
    app.run(
        host='0.0.0.0', 
        port=port, 
        debug=config.DEBUG_MODE
    )



