// MongoDB initialization script
// Creates the application user with minimal required privileges.

db.getSiblingDB("admin").auth(
  _getEnv("MONGO_INITDB_ROOT_USERNAME"),
  _getEnv("MONGO_INITDB_ROOT_PASSWORD")
);

db = db.getSiblingDB("dbt_platform");

db.createUser({
  user: "dbt_app",
  pwd: _getEnv("MONGODB_PASSWORD"),
  roles: [
    { role: "readWrite", db: "dbt_platform" },
  ],
});

print("Created dbt_app user on dbt_platform database.");
