"""
数据库初始化脚本
用于初始化 PostgreSQL 数据库表结构
"""

import sys
import os

def init_database(database_url=None):
    """
    初始化数据库表结构
    
    Args:
        database_url: PostgreSQL 数据库连接字符串
        
    Returns:
        bool: 是否初始化成功
    """
    print("=" * 50)
    print("正在初始化数据库...")
    
    # 如果没有提供URL，尝试从环境变量获取
    if not database_url:
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            print("❌ 未提供数据库连接字符串，且环境变量中无 DATABASE_URL")
            return False
    
    print(f"使用数据库连接: {database_url[:30]}...")
    
    try:
        # 尝试导入 PostgreSQL 驱动
        try:
            import psycopg2
        except ImportError:
            print("❌ 未安装 psycopg2-binary 包")
            print("请运行: pip install psycopg2-binary")
            return False
        
        # 连接数据库
        conn = psycopg2.connect(database_url)
        conn.autocommit = False
        cursor = conn.cursor()
        
        print("✅ 数据库连接成功")
        
        # 检查 schema.sql 文件
        sql_file = os.path.join(os.path.dirname(__file__), 'schema.sql')
        
        if os.path.exists(sql_file):
            print(f"📄 使用 SQL 文件: {sql_file}")
            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_content = f.read()
            
            # 分割 SQL 语句
            sql_statements = [stmt.strip() for stmt in sql_content.split(';') if stmt.strip()]
            
            for i, statement in enumerate(sql_statements, 1):
                if statement:
                    try:
                        cursor.execute(statement)
                        print(f"   ✅ 执行 SQL 语句 {i}/{len(sql_statements)}")
                    except Exception as e:
                        print(f"   ⚠️  语句 {i} 执行失败: {e}")
                        # 继续执行其他语句
        else:
            print("⚠️  未找到 schema.sql 文件，创建默认表结构")
            
            # 创建默认表结构
            default_tables = [
                # 激活码表
                """
                CREATE TABLE IF NOT EXISTS activations (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    activation_code TEXT NOT NULL UNIQUE,
                    product_type VARCHAR(50) DEFAULT 'personal',
                    days_valid INTEGER DEFAULT 365,
                    max_devices INTEGER DEFAULT 3,
                    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    valid_until TIMESTAMP NOT NULL,
                    is_used BOOLEAN DEFAULT FALSE,
                    used_at TIMESTAMP,
                    used_by_device TEXT,
                    purchase_id TEXT,
                    order_id TEXT,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """,
                
                # 设备激活表
                """
                CREATE TABLE IF NOT EXISTS device_activations (
                    id SERIAL PRIMARY KEY,
                    activation_id INTEGER REFERENCES activations(id) ON DELETE CASCADE,
                    device_id TEXT NOT NULL,
                    device_name TEXT,
                    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    UNIQUE(activation_id, device_id)
                )
                """,
                
                # 购买记录表
                """
                CREATE TABLE IF NOT EXISTS purchases (
                    id SERIAL PRIMARY KEY,
                    purchase_id TEXT UNIQUE NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    product_name TEXT,
                    price DECIMAL(10, 2),
                    currency VARCHAR(10),
                    purchased_at TIMESTAMP,
                    gumroad_data JSONB DEFAULT '{}',
                    processed BOOLEAN DEFAULT FALSE,
                    processed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            ]
            
            for i, table_sql in enumerate(default_tables, 1):
                try:
                    cursor.execute(table_sql)
                    print(f"   ✅ 创建表 {i}/{len(default_tables)}")
                except Exception as e:
                    print(f"   ⚠️  创建表 {i} 失败: {e}")
        
        # 创建索引
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_activations_code ON activations(activation_code)",
            "CREATE INDEX IF NOT EXISTS idx_activations_email ON activations(email)",
            "CREATE INDEX IF NOT EXISTS idx_purchases_purchase_id ON purchases(purchase_id)",
            "CREATE INDEX IF NOT EXISTS idx_device_activations ON device_activations(activation_id, device_id)"
        ]
        
        for i, index_sql in enumerate(indexes, 1):
            try:
                cursor.execute(index_sql)
                print(f"   📊 创建索引 {i}/{len(indexes)}")
            except Exception as e:
                print(f"   ⚠️  创建索引 {i} 失败: {e}")
        
        # 提交事务
        conn.commit()
        cursor.close()
        conn.close()
        
        print("✅ 数据库初始化完成")
        print("=" * 50)
        return True
        
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        if 'conn' in locals() and conn:
            try:
                conn.rollback()
                conn.close()
            except:
                pass
        return False

def main():
    """命令行入口"""
    if len(sys.argv) == 2:
        # 从命令行参数获取数据库URL
        database_url = sys.argv[1]
    else:
        # 从环境变量获取
        database_url = os.getenv('DATABASE_URL')
    
    if not database_url:
        print("❌ 请提供数据库连接字符串")
        print("用法: python init_db.py <database_url>")
        print("或设置环境变量 DATABASE_URL")
        sys.exit(1)
    
    success = init_database(database_url)
    
    if success:
        print("🎉 数据库初始化成功")
        sys.exit(0)
    else:
        print("💥 数据库初始化失败")
        sys.exit(1)

if __name__ == "__main__":
    main()