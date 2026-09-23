// MongoDB initialization script
// Creates the application user with minimal required privileges.

db.getSiblingDB("admin").auth(
  process.env.MONGO_INITDB_ROOT_USERNAME,
  process.env.MONGO_INITDB_ROOT_PASSWORD
);

db = db.getSiblingDB("dbt_platform");

db.createUser({
  user: "dbt_app",
  pwd: process.env.MONGODB_PASSWORD,
  roles: [
    { role: "readWrite", db: "dbt_platform" },
  ],
});

print("Created dbt_app user on dbt_platform database.");
